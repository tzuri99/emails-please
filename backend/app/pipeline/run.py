"""Headless pipeline runner: inbox -> per-email verdict -> submission.json.

Runs without FastAPI, Postgres or a browser, so the scored artefact can be
regenerated and diffed at any point:

    python -m app.pipeline.run ../../data -o submission.json
"""
import argparse
import json
import sys

from .classify import awaiting_document, classify, shipment_reference
from .compare import Result
from .verdict import compare_documents
from .extract import Unreadable, WrongDocType, extract
from .loader import Inbox


def _pick(attachments, marker):
    for a in attachments:
        if marker in a:
            return a
    return None


def process(email, inbox):
    """Return (category, Result, decided_by, reason)."""
    category, decided_by, reason = classify(email)
    if category != "BL_COMPARISON":
        return category, Result("OK"), decided_by, reason

    atts = email.get("attachments") or []
    si_path, bl_path = _pick(atts, "_SI"), _pick(atts, "_BL")

    if not si_path or not bl_path:
        which = "SI" if not si_path else "BL"
        return category, Result(
            "NEEDS_REVIEW", review_reason="missing_attachment",
            notes=[f"{which} attachment not present on the email"],
        ), decided_by, reason

    parsed, meta = {}, {}
    for tag, path in (("SI", si_path), ("BL", bl_path)):
        try:
            parsed[tag], _text, meta[tag] = extract(inbox.read_bytes(path), path, expect=tag)
        except WrongDocType as exc:
            return category, Result("NEEDS_REVIEW", review_reason="wrong_doc_type",
                                    notes=[f"{path}: {exc}"]), decided_by, reason
        except (Unreadable, OSError) as exc:
            return category, Result("NEEDS_REVIEW", review_reason="unreadable",
                                    notes=[f"{path}: {exc}"]), decided_by, reason
        except Exception as exc:
            # An unexpected reader failure is this document's problem, not
            # the whole run's. Escalate the case and keep going: losing 519
            # results to one bad file is never the right trade.
            return category, Result(
                "NEEDS_REVIEW", review_reason="unreadable",
                notes=[f"{path}: unexpected {type(exc).__name__}: {exc}"],
            ), decided_by, reason

    verdicts, status, defects, review_reason, confidence = compare_documents(
        parsed["SI"], parsed["BL"], meta.get("SI"), meta.get("BL"))
    result = Result(status, defect_fields=defects, review_reason=review_reason)
    result.verdicts = verdicts
    result.confidence = confidence
    return category, result, decided_by, reason


def run(source):
    inbox = Inbox(source)
    submission, detail = {}, {}
    for email in inbox:
        eid = email["email_id"]
        category, result, decided_by, reason = process(email, inbox)
        submission[eid] = {"category": category, **result.to_submission(),
                           "decided_by": decided_by}
        detail[eid] = {"subject": email.get("subject", ""),
                       "from": email.get("from", ""),
                       "route_reason": reason,
                       "confidence": getattr(result, "confidence", None),
                       "fields": [v.to_dict() for v in getattr(result, "verdicts", [])],
                       "notes": result.notes}
    return submission, detail


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="bundle folder or http://localhost:8080")
    ap.add_argument("-o", "--out", default="submission.json")
    ap.add_argument("--detail", help="also write per-email evidence here")
    args = ap.parse_args(argv)

    submission, detail = run(args.source)
    with open(args.out, "w") as fh:
        json.dump(submission, fh, indent=2)
    if args.detail:
        with open(args.detail, "w") as fh:
            json.dump(detail, fh, indent=2)

    print(f"{len(submission)} emails -> {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
