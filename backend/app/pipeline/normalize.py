"""Value normalisation.

Normalisation decides the false-positive rate, which is what the end-to-end
metric punishes. Two rules govern everything here:

  * Strip only formatting. Never strip meaning. `NANTONG, CHINA (CNNTG)` and
    `KARACHI, PAKISTAN (CNNTG)` share a locode but are a real discrepancy --
    defects are planted in the city name, so ports compare on the city text
    and the parenthesised code is discarded.
  * Party names compare on the full normalised string. `APRIL FINE PAPER
    TRADING` and `APRIL FINE PAPER TRADING (MIDDLE EAST) FZE` are distinct
    legal entities; prefix/fuzzy matching would hide a planted defect.
"""
import re

from .fields import BLANK_MARKERS

_PUNCT = re.compile(r"[.,;:'\"]")
_WS = re.compile(r"\s+")
_PAREN_CODE = re.compile(r"\(\s*[A-Z]{5}\s*\)")      # UN/LOCODE, e.g. (CNNTG)
_UNDERSCORES = re.compile(r"_{2,}")


def is_blank(raw):
    """True when the source document carries no usable value for the field."""
    if raw is None:
        return True
    v = raw.strip()
    if _UNDERSCORES.search(v):                        # `____` or `____MT`
        return True
    return v.lower() in BLANK_MARKERS


def _base(raw):
    v = _PUNCT.sub(" ", raw.upper())
    return _WS.sub(" ", v).strip()


def norm_party(raw):
    """Shipper / consignee / notify party."""
    return _base(raw)


def norm_port(raw):
    """Port of loading / discharge -- city text, locode discarded."""
    return _base(_PAREN_CODE.sub(" ", raw.upper()))


def norm_containers(raw):
    """`6 x 40'HC` -> 6.  Returns int, or None when no count is present."""
    m = re.search(r"\d+", raw.replace(",", ""))
    return int(m.group()) if m else None


def norm_weight_kg(raw):
    """`131,058 KG` -> 131058.  Converts MT/tonnes to kg. None when absent."""
    v = raw.upper().replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", v)
    if not m:
        return None
    n = float(m.group())
    if re.search(r"\b(MT|TON|TONNE|TONNES)\b", v):
        n *= 1000
    return int(round(n))


NORMALISERS = {
    "shipper": norm_party,
    "consignee": norm_party,
    "notify_party": norm_party,
    "port_of_loading": norm_port,
    "port_of_discharge": norm_port,
    "container_count": norm_containers,
    "gross_weight_kg": norm_weight_kg,
}


def normalise(field, raw):
    """Normalise `raw` for `field`. Returns None when the value is unusable."""
    if is_blank(raw):
        return None
    return NORMALISERS[field](raw)
