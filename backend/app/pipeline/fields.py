"""Canonical shipment fields and the label aliases seen across SI / BL documents.

The SI and BL label the same field differently ("Port of Loading" vs "Load
Port"), so extraction aligns by meaning via ALIASES, never by header text.
"""
import re

FIELDS = [
    "shipper",
    "consignee",
    "notify_party",
    "port_of_loading",
    "port_of_discharge",
    "container_count",
    "gross_weight_kg",
]


def canon_label(label):
    """Fold a raw document label to its ALIASES lookup key.

    Labels are not clean ASCII: one SI template ships a bilingual header that
    embeds CJK characters between the English words and the unit suffix, and
    it appears 51 times in the corpus. Non-ASCII is dropped and a space is
    forced before any parenthetical, so every spelling of a unit suffix
    collapses onto the same key.
    """
    ascii_only = "".join(ch for ch in label if ord(ch) < 128)
    spaced = re.sub(r"\s*\(", " (", ascii_only)
    # Stripping CJK can leave an empty or duplicate parenthetical behind:
    # "Consignee (CJK)" -> "Consignee ()", and
    # "Gross Wt (kgs) (CJK KGS)" -> "Gross Wt (kgs) ( KGS)".
    # Drop the empties, then keep only the first qualifier.
    spaced = re.sub(r"\(\s+", "(", spaced)      # "( KGS)" -> "(KGS)"
    spaced = re.sub(r"\s+\)", ")", spaced)
    spaced = re.sub(r"\(\s*\)", " ", spaced)   # "()" left by a CJK-only note
    parts = re.findall(r"\([^)]*\)", spaced)
    if len(parts) > 1:
        head = spaced[:spaced.index(parts[1])]
        spaced = head
    return re.sub(r"\s+", " ", spaced).strip().lower()


# canon_label(...) -> canonical field
ALIASES = {
    "shipper": "shipper",
    "shipper/exporter": "shipper",
    "shipper (principal or seller)": "shipper",
    "consignee": "consignee",
    "consignee (non-negotiable)": "consignee",
    "to the order of": "consignee",
    "notify": "notify_party",
    "notify party": "notify_party",
    "notify party/intermediate consignee": "notify_party",
    "port of loading": "port_of_loading",
    "port of loading (pol)": "port_of_loading",
    "pol": "port_of_loading",
    "load port": "port_of_loading",
    "port of discharge": "port_of_discharge",
    "port of discharge (pod)": "port_of_discharge",
    "pod": "port_of_discharge",
    "discharge port": "port_of_discharge",
    "total containers": "container_count",
    "no. of containers": "container_count",
    "no of containers": "container_count",
    "container count": "container_count",
    "no. of containers or packages": "container_count",
    "gross wt (kgs)": "gross_weight_kg",
    "gross wt (kg)": "gross_weight_kg",
    "gross weight": "gross_weight_kg",
    "gross weight (kg)": "gross_weight_kg",
    "gross weight (kgs)": "gross_weight_kg",   # bilingual header, CJK stripped
}
# Deliberately absent: "net weight". It is a different quantity and mapping it
# onto gross weight would invent discrepancies.

# Documents mark a value the customer left blank with a run of underscores.
BLANK_MARKERS = ("n/a", "tba", "-", "")


# Fallback label matching, tried only when the exact alias lookup misses.
#
# PDF text extraction can mangle a bilingual header's CJK run into filler
# ASCII -- "Gross Weight<CJK>(KGS)" comes out as "Gross Weightnn(KGS)" -- so
# the exact key no longer exists. These patterns key on the part of the
# label that survived.
#
# Order matters: "Notify Party/Intermediate Consignee" contains "consignee",
# so notify is tested first. Nothing here matches "net weight", which is a
# different quantity and must never fold into gross.
FUZZY_LABELS = [
    (re.compile(r"\bnotify"), "notify_party"),
    (re.compile(r"\bgross\s*w"), "gross_weight_kg"),
    (re.compile(r"\bno\.?\s*of\s+containers|\bcontainer\s*count|\btotal\s+containers"),
     "container_count"),
    (re.compile(r"\bport\s*of\s*loading|^pol\b|\bload\s*port"), "port_of_loading"),
    (re.compile(r"\bport\s*of\s*discharge|^pod\b|\bdischarge\s*port"), "port_of_discharge"),
    (re.compile(r"\bconsignee|\bto\s+the\s+order\s+of"), "consignee"),
    (re.compile(r"\bshipper"), "shipper"),
]


def field_for_label(label):
    """Map a raw document label to a canonical field.

    Returns (field, exact). `exact` is False when the label only matched by
    fallback, which means the document's header was damaged -- worth
    surfacing rather than hiding.
    """
    key = canon_label(label)
    if key in ALIASES:
        return ALIASES[key], True
    for pattern, field in FUZZY_LABELS:
        if pattern.search(key):
            return field, False
    return None, False
