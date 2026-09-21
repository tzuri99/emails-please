"""Field-aware traced normalisation.

Same semantics as `normalize.py`, but every transformation is recorded so the
UI can show the journey from page text to compared value. `normalize.py`
stays as the plain fast path and the two must agree -- a test pins that.
"""
import re

from .trace import OCR_CONFUSIONS, Trace, detect_unit

_PUNCT = re.compile(r"[.,;:'\"]")
_WS = re.compile(r"\s+")
_PAREN_CODE = re.compile(r"\(\s*[A-Z]{5}\s*\)")
_UNDERSCORES = re.compile(r"_{2,}")
_BLANKS = {"n/a", "tba", "-", ""}


def _blank(raw, tr):
    if raw is None:
        tr.blank = True
        return True
    v = raw.strip()
    if _UNDERSCORES.search(v):
        tr.add("detect_blank", raw, "(blank)", "customer left this field empty")
        tr.blank = True
        return True
    if v.lower() in _BLANKS:
        tr.add("detect_blank", raw, "(blank)", "no value supplied")
        tr.blank = True
        return True
    return False


def _cosmetic(raw, tr):
    v = tr.add("trim", raw, raw.strip())
    v = tr.add("uppercase", v, v.upper())
    v = tr.add("strip_punctuation", v, _PUNCT.sub(" ", v))
    return tr.add("collapse_whitespace", v, _WS.sub(" ", v).strip())


def resolve_party(raw, tr):
    """Shipper / consignee / notify party -- full string, no fuzzy matching.

    Distinct legal entities differ by a suffix (`... (MIDDLE EAST) FZE`), so
    anything that tolerates a near-match here hides a real discrepancy.
    """
    return _cosmetic(raw, tr)


def resolve_port(raw, tr):
    """Ports compare on city text; the parenthesised UN/LOCODE is dropped.

    Defects in this corpus are planted in the city while the locode is left
    untouched, so comparing on the code would mask every port discrepancy.
    """
    v = raw.upper()
    stripped = _PAREN_CODE.sub(" ", v)
    tr.add("strip_locode", v, stripped)
    return _cosmetic(stripped, tr)


def resolve_containers(raw, tr):
    v = tr.add("strip_thousands", raw, raw.replace(",", ""))
    m = re.search(r"\d+", v)
    if not m:
        repaired = v.translate(OCR_CONFUSIONS)
        m = re.search(r"\d+", repaired)
        if m:
            tr.add("ocr_repair", v, repaired, "no digits found until characters were repaired")
        else:
            return None
    tr.add("parse_number", v, m.group(), "took the leading count")
    tr.value = int(m.group())
    return tr.value


def resolve_weight(raw, tr):
    """Weight resolves to kilograms, recording the source unit.

    The unit is kept on the trace even after conversion: two values that
    share digits but not units (`50,000 kg` / `50,000 lb`) are a real
    difference *and* a likely transcription artifact, and the comparator
    needs both facts to describe it honestly.
    """
    unit, factor = detect_unit(raw)
    v = tr.add("strip_thousands", raw, raw.replace(",", ""))
    m = re.search(r"\d+(?:\.\d+)?", v)
    if not m:
        return None
    tr.add("parse_number", v, m.group())
    n = float(m.group())
    tr.unit = unit or "kg"
    if factor and factor != 1.0:
        converted = n * factor
        tr.add("unit_convert", f"{n} {unit}", f"{converted:.0f} kg",
               f"source stated {unit}; normalised to kilograms")
        n = converted
    tr.value = int(round(n))
    return tr.value


RESOLVERS = {
    "shipper": resolve_party,
    "consignee": resolve_party,
    "notify_party": resolve_party,
    "port_of_loading": resolve_port,
    "port_of_discharge": resolve_port,
    "container_count": resolve_containers,
    "gross_weight_kg": resolve_weight,
}


def resolve(field, raw):
    """Return a Trace carrying the comparable value and the full journey."""
    tr = Trace(raw=raw if raw is not None else "")
    if _blank(raw, tr):
        return tr
    out = RESOLVERS[field](raw, tr)
    if tr.value is None:
        tr.value = out
    return tr
