"""Stage 1 -- route each email to one of the five categories.

Rules first, LLM only as a tie-breaker. Subjects in this corpus are
deliberately misleading (an advance-fee scam arrives under
`Re: Invoice payment - kindly confirm your bank details`; a berthing report
under `_Reminder_Paper - Submit SI & AED_`), so evidence is weighted
sender > body > subject, and the subject is never decisive on its own.
"""
import re

CATEGORIES = ["BL_COMPARISON", "SI_REQUEST", "INVOICE_QUERY", "GENERAL", "SPAM"]

# Senders that appear only on junk. Highest-precision signal in the dataset.
SPAM_DOMAINS = {
    "webmail-verify.co", "secure-mailbox.org", "parcel-track.co",
    "logistics-deals.biz", "prize-claims.info", "crypto-invest.net",
}

SPAM_BODY = re.compile(
    r"congratulations!!!|monthly draw|limited time offer|90% off|"
    r"brand new iphone|unpaid customs fee|bank officer with an urgent|"
    r"guaranteed \d+% returns|exceeded its storage limit|verify your account",
    re.I,
)

# A genuine comparison request: two documents supplied (or explicitly expected)
# and the ask is to check one against the other.
COMPARE_BODY = re.compile(
    r"check the draft bl against the si|"
    r"attached are the si and draft bl|"
    r"attached the shipping instruction and the draft bill of lading|"
    r"please compare the si and draft bl|"
    r"attached si and draft bl|"
    r"confirm the bl is in order",
    re.I,
)

# A request to *produce* a document. Nothing to compare yet -- per the
# flowchart these share one node, and neither carries attachments.
REQUEST_BODY = re.compile(
    r"please find shipping instruction for|"
    r"assist to send the draft bl|"
    r"kindly issue the shipping instruction|"
    r"request si\b",
    re.I,
)

# A request for a document we do not have. Distinct from the SI requests
# that carry the shipment details inline: nothing here can be compared until
# someone sends the file, so the useful action is to ask for it.
AWAITING_DOCUMENT = re.compile(
    r"assist to send the draft bl|"
    r"please send (?:us )?the (?:draft )?(?:bl|b/l|bill of lading|si)|"
    r"kindly send the (?:draft )?(?:bl|si)|"
    r"awaiting the (?:draft )?(?:bl|si)",
    re.I,
)

# Shipment references as they appear in this corpus.
REFERENCE = re.compile(
    r"\b("
    r"[A-Z]{2,10}\d{6,14}"          # SIN832764835, PSGSE9638346, ONEYSINF32871
    r"|\d[A-Z]{3}-\d{5}"           # 5ALT-01226
    r"|\d{12}"                     # 070500236763
    r")\b"
)


def shipment_reference(email):
    """Best shipment reference in an email, for quoting back in a reply.

    The body is searched before the subject: the subject often carries a
    different reference from the one the sender is actually asking about.
    """
    for text in (email.get("body") or "", email.get("subject") or ""):
        match = REFERENCE.search(text)
        if match:
            return match.group(1)
    return None


INVOICE_BODY = re.compile(
    r"query on invoice|local charge|\bthc\b|d&d ?/ ?detention|detention charges|"
    r"gr is still missing|post the gr|before we release payment|invoice \d{8,}",
    re.I,
)


def _domain(addr):
    return addr.rsplit("@", 1)[-1].strip().lower() if "@" in addr else ""


def _looks_like_si_bl_pair(attachments):
    return any("_SI" in a for a in attachments) and any("_BL" in a for a in attachments)


def awaiting_document(email):
    """True when the sender is asking us for a document we do not hold."""
    if email.get("attachments"):
        return False
    return bool(AWAITING_DOCUMENT.search(email.get("body") or ""))


def classify(email):
    """Return (category, decided_by, reason).

    `decided_by` is "rule" or "llm"; the scorer reports the rule share as a
    diagnostic, and it tells us how much of the inbox never needs a model.
    """
    body = email.get("body") or ""
    atts = email.get("attachments") or []
    domain = _domain(email.get("from") or "")

    if domain in SPAM_DOMAINS:
        return "SPAM", "rule", f"sender domain {domain} is junk-only"
    if SPAM_BODY.search(body):
        return "SPAM", "rule", "body matches a known scam/marketing template"

    # Attachments are strong evidence, but only when they are an SI/BL pair.
    if _looks_like_si_bl_pair(atts) or COMPARE_BODY.search(body):
        return "BL_COMPARISON", "rule", "SI/BL pair supplied or comparison explicitly requested"

    # A lone SI, with the body asking for a comparison -> still a comparison
    # request; the missing BL is a reliability problem, not a routing one.
    if atts and any("_SI" in a for a in atts) and "compare" in body.lower():
        return "BL_COMPARISON", "rule", "comparison requested, BL attachment absent"

    if REQUEST_BODY.search(body):
        return "SI_REQUEST", "rule", "asks for a document to be produced"
    if INVOICE_BODY.search(body):
        return "INVOICE_QUERY", "rule", "invoice / charges / GR query"

    return "GENERAL", "rule", "no actionable document or billing request found"
