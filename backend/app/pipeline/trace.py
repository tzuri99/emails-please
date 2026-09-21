"""Traced normalisation: what we did to a value, and how sure we are.

The pipeline's job is not only to decide match/mismatch but to show its
working. Every transformation applied to a raw document value is recorded as
a Step, so the UI can replay the journey from what the page said to what the
comparator saw, and a reviewer can see exactly which interpretation to
overrule.

Confidence is derived from that trace rather than invented. The rule is:
certainty falls as the number of *interpretive* steps rises. Stripping
whitespace is free. Deciding that `50,000 lb` and `50,000 kg` are the same
number in different units is not.
"""
import re
from dataclasses import dataclass, field as dc_field

# Cosmetic steps cost nothing -- they cannot change meaning.
COSMETIC = {"trim", "collapse_whitespace", "uppercase", "strip_punctuation"}

# Interpretive steps each carry a confidence penalty and a plain-English
# reason the UI shows verbatim.
PENALTIES = {
    "strip_locode":      (0.02, "matched on city name; UN/LOCODE in parentheses ignored"),
    "parse_number":      (0.01, "read a number out of surrounding text"),
    "strip_thousands":   (0.00, "removed thousands separators"),
    "unit_convert":      (0.18, "converted units to compare; source units differed"),
    "ocr_repair":        (0.22, "repaired characters commonly confused by OCR"),
    "drop_non_ascii":    (0.03, "removed non-ASCII characters from a bilingual label"),
    "ocr_source":        (0.10, "value was read from a scanned page by OCR"),
    "ocr_spacing":       (0.15, "values differ only by spacing introduced by OCR"),
}

UNIT_PATTERNS = [
    ("kg", re.compile(r"\b(KGS?|KILOS?|KILOGRAMS?)\b", re.I), 1.0),
    ("mt", re.compile(r"\b(MT|TONNES?|TONS?|METRIC TONS?)\b", re.I), 1000.0),
    ("lb", re.compile(r"\b(LBS?|POUNDS?)\b", re.I), 0.45359237),
]

# Characters an OCR pass routinely confuses. Applied only when a value would
# otherwise fail to parse -- never speculatively.
OCR_CONFUSIONS = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "S": "5", "B": "8"})


@dataclass
class Step:
    """One transformation, with the value before and after."""
    name: str
    before: str
    after: str
    note: str = ""

    @property
    def changed(self):
        return self.before != self.after

    @property
    def interpretive(self):
        return self.name not in COSMETIC

    def to_dict(self):
        return {"name": self.name, "before": self.before, "after": self.after,
                "note": self.note, "changed": self.changed,
                "interpretive": self.interpretive}


@dataclass
class Trace:
    """The full journey of one raw value, plus what it cost in certainty."""
    raw: str
    steps: list = dc_field(default_factory=list)
    value: object = None
    unit: str = None
    blank: bool = False
    source: str = "text"          # "text" | "ocr" -- how the value was read
    snippet: str = None           # the document line this value was read from
    locator: str = None           # where that line sits: "line 7", "row 5" 

    def add(self, name, before, after, note=""):
        self.steps.append(Step(name, str(before), str(after), note))
        return after

    @property
    def applied(self):
        """Steps that actually altered the value."""
        return [s for s in self.steps if s.changed]

    @property
    def penalty(self):
        total = 0.0
        for s in self.applied:
            if s.interpretive:
                total += PENALTIES.get(s.name, (0.05, ""))[0]
        if self.source == "ocr":
            # Provenance, not a transformation: nothing was changed, but a
            # value lifted off a scan is inherently less certain.
            total += PENALTIES["ocr_source"][0]
        return total

    @property
    def reasons(self):
        out = []
        if self.source == "ocr":
            out.append(PENALTIES["ocr_source"][1])
        for s in self.applied:
            if s.interpretive and s.name in PENALTIES:
                out.append(PENALTIES[s.name][1])
        return out

    def to_dict(self):
        return {"raw": self.raw, "value": self.value, "unit": self.unit,
                "blank": self.blank, "source": self.source,
                "snippet": self.snippet, "locator": self.locator,
                "penalty": round(self.penalty, 3),
                "reasons": self.reasons,
                "steps": [s.to_dict() for s in self.steps]}


def detect_unit(text):
    """Return (unit_name, factor_to_kg) or (None, None)."""
    for name, pat, factor in UNIT_PATTERNS:
        if pat.search(text):
            return name, factor
    return None, None
