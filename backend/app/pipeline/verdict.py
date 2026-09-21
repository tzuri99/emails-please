"""Explainable comparison: per-field verdicts with confidence and rationale.

Every field produces a verdict a human can audit without opening the source
documents: what each side said, what we turned it into, whether we call that
a discrepancy, how sure we are, and why.

Confidence is a statement about *our reading*, not about the shipment. A
confident mismatch means the documents plainly disagree. A low-confidence
mismatch means they may only appear to disagree because of how we read them
-- and that is a question for a person, not a defect to report.
"""
from dataclasses import dataclass, field as dc_field

from .fields import FIELDS
from .resolve import resolve

# Below this, we do not trust our own reading enough to call it a defect.
# Tuned so an ordinary mismatch (~0.97) never escalates while a unit
# artifact (~0.55) always does.
ESCALATION_THRESHOLD = 0.70

# A same-digits/different-units pair is the classic transcription artifact:
# `50,000 kg` against `50,000 lb`. Genuinely different masses, but far more
# likely a unit dropped in transcription than a real loading error.
UNIT_ARTIFACT_DISCOUNT = 0.25

# OCR splits words that belong together -- "AL GURG" is read as "AL GU RG".
# When two values are identical once every space is removed, and at least one
# came from a scan, that is the engine's spacing rather than a real
# difference. Content differences survive this test untouched, so a genuine
# discrepancy is never hidden by it.
OCR_SPACING_PENALTY = 0.15

# Two OCR'd values this similar differ by a handful of characters. That is
# indistinguishable from a misread, so the case is escalated rather than
# reported. Below this, the values are genuinely different text.
OCR_NOISE_SIMILARITY = 0.82
OCR_NOISE_DISCOUNT = 0.35

NUMERIC_FIELDS = {"container_count", "gross_weight_kg"}

# A field whose header only matched by fallback carries some doubt about
# whether the two sides describe the same thing. Deliberately small: it has
# to stay ABOVE the escalation threshold, because two plainly different
# numbers are still a defect even when the header was read loosely. A larger
# discount turned two real discrepancies into escalations. The tag carries
# the doubt; the threshold is reserved for what we genuinely cannot call.
LABEL_MAPPING_DISCOUNT = 0.15

# Why two values differ, which is a different question from whether they do.
# The reviewer's next action depends entirely on this: a genuine mismatch goes
# back to the shipper, a misread goes back to the scanner, and a mapping
# problem means we may not be comparing the right two fields at all.
KIND = {
    "match": "Match",
    "formatting": "Formatting difference",
    "unit": "Unit mismatch",
    "ocr_misread": "Likely OCR misread",
    "label_mapping": "Label mapping issue",
    "genuine": "Genuine mismatch",
    "not_stated": "Not stated",
}


@dataclass
class FieldVerdict:
    field: str
    si: object                      # Trace
    bl: object                      # Trace
    verdict: str                    # MATCH | MISMATCH | UNCOMPARABLE
    confidence: float
    rationale: list = dc_field(default_factory=list)
    artifact: str = None            # e.g. "unit_artifact"
    kind: str = "match"             # a key of KIND

    @property
    def uncertain(self):
        return self.verdict == "MISMATCH" and self.confidence < ESCALATION_THRESHOLD

    def to_dict(self):
        return {
            "field": self.field, "verdict": self.verdict,
            "confidence": round(self.confidence, 3),
            "rationale": self.rationale, "artifact": self.artifact,
            "kind": self.kind, "kind_label": KIND.get(self.kind, self.kind),
            "uncertain": self.uncertain,
            "si": self.si.to_dict(), "bl": self.bl.to_dict(),
        }


def _unit_artifact(si, bl):
    """Same digits on both sides but different stated units."""
    if si.unit is None or bl.unit is None or si.unit == bl.unit:
        return False
    si_digits = next((s.after for s in si.steps if s.name == "parse_number"), None)
    bl_digits = next((s.after for s in bl.steps if s.name == "parse_number"), None)
    return si_digits is not None and si_digits == bl_digits


def _despaced(value):
    return "".join(str(value).split()) if value is not None else None


def _digits(raw):
    return "".join(ch for ch in str(raw or "") if ch.isdigit())


def _similarity(a, b):
    from difflib import SequenceMatcher
    return SequenceMatcher(None, str(a), str(b)).ratio()


def compare_field(field, si_raw, bl_raw, si_ocr=False, bl_ocr=False,
                  inexact_label=False, si_where=None, bl_where=None):
    si, bl = resolve(field, si_raw), resolve(field, bl_raw)
    for trace, is_ocr, where in ((si, si_ocr, si_where), (bl, bl_ocr, bl_where)):
        if is_ocr:
            trace.source = "ocr"
        if where:
            trace.snippet = where.get("snippet")
            trace.locator = where.get("locator")
    base = max(0.0, 1.0 - si.penalty - bl.penalty)
    rationale = []

    if si.blank or bl.blank:
        which = "SI" if si.blank else "BL"
        return FieldVerdict(field, si, bl, "UNCOMPARABLE", 0.0,
                            [f"the {which} does not state a value for this field"],
                            kind="not_stated")

    if si.value is None or bl.value is None:
        which = "SI" if si.value is None else "BL"
        return FieldVerdict(field, si, bl, "UNCOMPARABLE", 0.0,
                            [f"could not read a usable value from the {which}"],
                            kind="not_stated")

    rationale.extend(f"SI: {r}" for r in si.reasons)
    rationale.extend(f"BL: {r}" for r in bl.reasons)

    if si.value == bl.value:
        # "Formatting difference" must mean the two DOCUMENTS were written
        # differently, not that our normaliser happened to run. Stripping a
        # comma from both sides of an identical string is not a difference,
        # and tagging it as one makes clean rows look flagged.
        differed = si.raw.strip() != bl.raw.strip()
        note = ("values agree once normalised" if differed
                else "values agree exactly")
        return FieldVerdict(field, si, bl, "MATCH", base, [note] + rationale,
                            kind="formatting" if differed else "match")

    if si_ocr or bl_ocr:
        # Identical content, different spacing: "AL GU RG" / "AL GURG".
        if _despaced(si.value) == _despaced(bl.value):
            return FieldVerdict(
                field, si, bl, "MATCH", max(0.0, base - OCR_SPACING_PENALTY),
                ["values match apart from spacing introduced by OCR "
                 f"({si.value!r} vs {bl.value!r})"] + rationale,
                artifact="ocr_spacing", kind="formatting")

        # Same digits, different separator: "237,750" / "237.750". Only the
        # digits are compared, so a real change of quantity still differs.
        if field in NUMERIC_FIELDS:
            si_digits, bl_digits = _digits(si.raw), _digits(bl.raw)
            if si_digits and si_digits == bl_digits:
                return FieldVerdict(
                    field, si, bl, "MATCH", max(0.0, base - OCR_SPACING_PENALTY),
                    ["identical digits with a different separator "
                     f"({si.raw.strip()!r} vs {bl.raw.strip()!r}); OCR confuses "
                     "'.' and ','"] + rationale,
                    artifact="ocr_separator", kind="formatting")

        # Nearly the same string. "NHAWA SHEVA" against "NHAVA SHEVA" is
        # either a real change of port or one substituted character, and
        # nothing in the documents tells us which. That is a question for a
        # person, so it is reported as a difference we cannot vouch for
        # rather than as a defect.
        similarity = _similarity(si.value, bl.value)
        if similarity >= OCR_NOISE_SIMILARITY:
            return FieldVerdict(
                field, si, bl, "MISMATCH", max(0.0, base - OCR_NOISE_DISCOUNT),
                [f"values differ by a few characters ({si.value!r} vs "
                 f"{bl.value!r}); both documents were read by OCR, so this may "
                 "be a misread rather than a real discrepancy"] + rationale,
                artifact="ocr_noise", kind="ocr_misread")

    artifact, confidence, kind = None, base, "genuine"
    if field == "gross_weight_kg" and _unit_artifact(si, bl):
        artifact, kind = "unit_artifact", "unit"
        confidence = max(0.0, base - UNIT_ARTIFACT_DISCOUNT)
        rationale.insert(0, f"identical figures under different units "
                            f"({si.unit.upper()} vs {bl.unit.upper()}) -- likely a "
                            f"transcription artifact rather than a loading difference")
    elif inexact_label:
        # One side's header only matched by fallback, so these may not be the
        # same field. Reported as a mapping problem rather than a discrepancy,
        # and discounted so it escalates instead of accusing the shipper.
        kind = "label_mapping"
        confidence = max(0.0, base - LABEL_MAPPING_DISCOUNT)
        rationale.insert(0, "the label for this field matched only by fallback on "
                            "one document, so the two values may not describe the "
                            f"same field ({si.value!r} vs {bl.value!r})")
    else:
        rationale.insert(0, f"SI reads {si.value!r}, BL reads {bl.value!r}")

    return FieldVerdict(field, si, bl, "MISMATCH", confidence, rationale,
                        artifact, kind)


def compare_documents(si_fields, bl_fields, si_meta=None, bl_meta=None):
    """Compare all seven fields.

    `si_meta` / `bl_meta` carry how each document was read, so a value
    recovered from a scan is discounted relative to one lifted from a text
    layer. Returns (verdicts, status, defect_fields, review_reason,
    overall_confidence).
    """
    si_ocr = (si_meta or {}).get("source") == "ocr"
    bl_ocr = (bl_meta or {}).get("source") == "ocr"
    inexact = (set((si_meta or {}).get("inexact_labels") or [])
               | set((bl_meta or {}).get("inexact_labels") or []))
    si_src = (si_meta or {}).get("sources") or {}
    bl_src = (bl_meta or {}).get("sources") or {}
    verdicts = [compare_field(f, si_fields.get(f), bl_fields.get(f), si_ocr, bl_ocr,
                              inexact_label=f in inexact,
                              si_where=si_src.get(f), bl_where=bl_src.get(f))
                for f in FIELDS]

    uncomparable = [v for v in verdicts if v.verdict == "UNCOMPARABLE"]
    mismatches = [v for v in verdicts if v.verdict == "MISMATCH"]
    uncertain = [v for v in mismatches if v.uncertain]

    scored = [v.confidence for v in verdicts if v.verdict != "UNCOMPARABLE"]
    overall = round(min(scored), 3) if scored else 0.0

    if uncomparable:
        return verdicts, "NEEDS_REVIEW", [], "missing_value", overall
    if uncertain:
        # We can see a difference but cannot vouch for our reading of it.
        # When that doubt comes from OCR the problem is legibility, not a
        # missing value, so it is reported as unreadable -- which is what a
        # reviewer is actually being asked to resolve.
        from_ocr = any(v.artifact == "ocr_noise" for v in uncertain)
        reason = "unreadable" if from_ocr else "missing_value"
        return verdicts, "NEEDS_REVIEW", [], reason, overall
    if mismatches:
        return verdicts, "MISMATCH", sorted(v.field for v in mismatches), None, overall
    return verdicts, "OK", [], None, overall
