"""Stage 2 -- pull the seven shipment fields out of an SI or BL document.

Format handling lives in `extractors.py`; this module owns what the document
*is* (SI, BL, or an impostor) and guarantees the caller either gets usable
fields or an explanation of why not.
"""
import re

from .extractors import Unreadable, read
from .fields import ALIASES, FIELDS, canon_label


class WrongDocType(Exception):
    """Document parsed, but it is not the SI or BL we were promised."""


# A document declares itself in its first few lines. Order matters: a
# "BILL OF LADING INSTRUCTION" is a Shipping Instruction -- it instructs the
# carrier on what to put *on* the BL -- so the instruction patterns are
# tested before the bare "bill of lading" that they contain.
DOC_TITLES = (
    ("SI", re.compile(r"(shipping|bill of lading|b/l)\s+instruction", re.I)),
    ("SI", re.compile(r"shipping instruction", re.I)),
    ("BL", re.compile(r"bill of lading", re.I)),
)
IMPOSTORS = re.compile(r"commercial invoice|packing list|certificate of origin", re.I)

_LABEL_LINE = re.compile(r"^(?P<label>[^:\n]{2,60}):(?P<value>.*)$")


def detect_doc_type(text):
    """Return 'SI', 'BL', or the impostor's name from the document header."""
    head = "\n".join(text.splitlines()[:6])
    if IMPOSTORS.search(head):
        return IMPOSTORS.search(head).group(0).upper()
    for kind, pat in DOC_TITLES:
        if pat.search(head):
            return kind
    return "UNKNOWN"


def parse_text(text):
    """Map canonical field -> raw value string, first occurrence wins.

    Continuation lines (indented address lines) are skipped: only the first
    line of a block carries the value we compare on.
    """
    found = {}
    for line in text.splitlines():
        if not line or line[0].isspace():
            continue
        m = _LABEL_LINE.match(line)
        if not m:
            continue
        label = canon_label(m.group("label"))
        field = ALIASES.get(label)
        if field and field not in found:
            found[field] = m.group("value").strip()
    return found


def extract(raw_bytes, filename, expect=None):
    """Extract the seven fields from one attachment, whatever its format.

    `expect` is 'SI' or 'BL'. Raises WrongDocType when the document announces
    itself as something else, Unreadable when it cannot be read at all.
    """
    fields, text, meta = read(raw_bytes, filename)    # may raise Unreadable

    # Escalate only when the attachment is a different *kind* of document --
    # an invoice or packing list standing in for the BL. An SI/BL title
    # disagreement is not reliable enough to act on: templates vary, and the
    # attachment's role is already established by the email that carried it.
    kind = detect_doc_type(text)
    if kind not in ("SI", "BL", "UNKNOWN"):
        raise WrongDocType(f"expected {expect or 'SI/BL'}, document is a {kind.title()}")

    if not fields:
        raise Unreadable("no recognisable shipment fields in this document")

    return fields, text, meta
