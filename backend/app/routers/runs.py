"""Kick off pipeline runs and read their results."""
import logging
import time
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import func, or_ as sa_or, select
from sqlalchemy.orm import Session, selectinload

from ..auth import RequireUser
from ..config import settings
from ..db import SessionLocal, get_db
from ..models import EmailResult, Review, Run
from ..pipeline.loader import Inbox
from ..pipeline.classify import awaiting_document, shipment_reference
from ..pipeline.run import process
from ..schemas import EmailDetail, EmailSummary, RunOut

router = APIRouter(prefix="/api/runs", tags=["runs"])
log = logging.getLogger("sdoc.runs")


PROGRESS_EVERY = 50


def _standing_reviews(db):
    """The decision that currently stands for each email, across all runs.

    Reviews hang off EmailResult rows, and every run creates new ones, so
    "the standing decision" is the most recent review for an email_id
    regardless of which run's row it was recorded against.
    """
    rows = db.execute(
        select(EmailResult.email_id, Review)
        .join(Review, Review.result_id == EmailResult.id)
        .order_by(Review.created_at, Review.id)
    ).all()
    return {email_id: review for email_id, review in rows}   # last one wins


def _carry_forward(standing):
    """Copy a decision onto a fresh run's row.

    The original timestamp and reviewer are preserved: this is the same
    human decision, not a new one, and an audit trail that re-dates itself
    on every re-run is not an audit trail.
    """
    return Review(
        decision=standing.decision,
        machine_status=standing.machine_status,
        final_status=standing.final_status,
        final_defect_fields=standing.final_defect_fields,
        reviewer=standing.reviewer,
        note=standing.note,
        seconds_to_decide=standing.seconds_to_decide,
        created_at=standing.created_at,
    )


def _execute(run_id: int, source: str):
    """Run the pipeline and persist every verdict.

    Failures are recorded on the Run rather than swallowed: the brief asks
    for processing failures to be visible and retryable, so a crashed run
    stays in the list with its error attached.

    All 520 rows are added to one session and committed once at the end.
    That is deliberate -- a per-email commit would be 520 round trips, and
    on a managed Postgres across the network that is the difference between
    seconds and minutes. It also means a run is atomic: it either lands
    completely or not at all, so no one triages half an inbox.
    """
    db = SessionLocal()
    run = None
    started = time.monotonic()
    try:
        run = db.get(Run, run_id)
        if run is None:
            # Deleted between the POST and the background task starting.
            # Nothing to mark, and nothing to write results against.
            log.error("run %s no longer exists; abandoning", run_id)
            return
        inbox = Inbox(source)
        emails = inbox.emails()
        run.total_emails = len(emails)
        log.info("run %s: starting, %s emails from %s", run_id, len(emails), source)

        # Carry standing human decisions onto this run.
        #
        # A review belongs to an EmailResult, and a re-run builds new ones.
        # Without this, every decision an operator has made vanishes from
        # the queue the moment they press "Run again" -- the rows survive
        # against the old run, but the UI only ever shows the newest one.
        # Worse, /submission reads the current run, so the exported answer
        # silently reverts to the machine's verdict and loses every human
        # correction.
        #
        # Only the standing decision is carried, not the full history: the
        # earlier run keeps its own audit trail where it was recorded.
        carried = _standing_reviews(db)
        if carried:
            log.info("run %s: carrying forward %s standing decision(s)",
                     run_id, len(carried))

        for n, email in enumerate(emails, 1):
            category, result, _decided_by, reason = process(email, inbox)
            row = EmailResult(
                run_id=run.id,
                email_id=email["email_id"],
                sender=email.get("from", ""),
                subject=email.get("subject", ""),
                body=email.get("body", ""),
                attachments=email.get("attachments", []),
                category=category,
                route_reason=reason,
                status=result.status,
                review_reason=result.review_reason,
                defect_fields=result.defect_fields,
                confidence=getattr(result, "confidence", None),
                awaiting_document=awaiting_document(email),
                shipment_reference=shipment_reference(email),
                field_verdicts=[v.to_dict() for v in getattr(result, "verdicts", [])],
                notes=result.notes,
            )
            standing = carried.get(email["email_id"])
            if standing is not None:
                row.reviews.append(_carry_forward(standing))
            db.add(row)

            # Heartbeat. Without it a run that dies at email 300 is
            # indistinguishable in the logs from one that never started.
            if n % PROGRESS_EVERY == 0 or n == len(emails):
                log.info("run %s: %s/%s emails (%.1fs elapsed)",
                         run_id, n, len(emails), time.monotonic() - started)

        run.status = "succeeded"
        log.info("run %s: committing %s results", run_id, len(emails))
    except Exception as exc:                        # surfaced, not hidden
        log.exception("run %s failed", run_id)
        # The session may be in a failed transaction, in which case every
        # later statement -- including the one that records the failure --
        # is rejected. Roll back first, then re-read the row.
        try:
            db.rollback()
            run = db.get(Run, run_id)
        except Exception:
            log.exception("run %s: session unusable after failure", run_id)
            run = None
        if run is not None:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
    finally:
        # Recording the outcome must not itself raise: if the database went
        # away mid-run, this is the second failure and the useful thing is
        # a log line, not a traceback into a background task nobody awaits.
        # A run left "running" by a hard crash is swept on next startup.
        try:
            if run is not None:
                run.finished_at = datetime.now(timezone.utc)
                db.commit()
                log.info("run %s: %s after %.1fs",
                         run_id, run.status, time.monotonic() - started)
        except Exception:
            log.exception("run %s: could not record its own outcome", run_id)
        finally:
            db.close()


@router.post("", response_model=RunOut, status_code=202)
def start_run(background: BackgroundTasks, db: Session = Depends(get_db),
              source: str | None = None, user=RequireUser):
    run = Run(source=source or settings.data_source, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(_execute, run.id, run.source)
    return run


@router.get("", response_model=list[RunOut])
def list_runs(db: Session = Depends(get_db), limit: int = 20):
    return db.scalars(select(Run).order_by(Run.id.desc()).limit(limit)).all()


@router.get("/{run_id}", response_model=RunOut)
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(Run, run_id)
    if not run:
        raise HTTPException(404, "run not found")
    return run


@router.get("/{run_id}/stats")
def run_stats(run_id: int, db: Session = Depends(get_db)):
    """Counts the triage header needs, in one query per axis."""
    def tally(col):
        rows = db.execute(
            select(col, func.count()).where(EmailResult.run_id == run_id).group_by(col)
        ).all()
        return {k: n for k, n in rows}

    reviewed = db.scalar(
        select(func.count()).select_from(EmailResult)
        .where(EmailResult.run_id == run_id, EmailResult.reviews.any())
    )
    # One cross-tab drives three things: the state counts, the type counts,
    # and which state/type pairs exist at all. Deriving them from separate
    # queries is how the header and the sidebar drifted apart.
    rows = db.execute(
        select(EmailResult.status, EmailResult.review_reason,
               EmailResult.category, EmailResult.awaiting_document, func.count())
        .where(EmailResult.run_id == run_id)
        # Every selected column must be grouped. SQLite silently returns an
        # arbitrary row's value for an ungrouped column -- which attributed
        # all 216 SI requests to a state only 91 belong in -- and Postgres
        # rejects the query outright.
        .group_by(EmailResult.status, EmailResult.review_reason,
                  EmailResult.category, EmailResult.awaiting_document)
    ).all()

    by_state, by_state_category = {}, {}
    for status, reason, category, awaiting, n in rows:
        if reason == "wrong_doc_type":
            key = "WRONG_DOCUMENT"
        elif awaiting or reason == "missing_attachment":
            key = "MISSING_ATTACHMENT"
        elif status == "NEEDS_REVIEW" and reason in EmailResult.UNREADABLE_REASONS:
            key = "UNREADABLE"
        else:
            key = status
        by_state[key] = by_state.get(key, 0) + n
        bucket = by_state_category.setdefault(key, {})
        bucket[category] = bucket.get(category, 0) + n

    return {"by_category": tally(EmailResult.category),
            "by_status": tally(EmailResult.status),
            "by_state": by_state,
            "by_state_category": by_state_category,
            "by_review_reason": tally(EmailResult.review_reason),
            "reviewed": reviewed or 0}


@router.get("/{run_id}/emails", response_model=list[EmailSummary])
def list_emails(run_id: int, db: Session = Depends(get_db),
                category: str | None = None, status: str | None = None,
                state: str | None = None, unreviewed: bool = False,
                reviewed: bool = False, limit: int = 600):
    q = (select(EmailResult).where(EmailResult.run_id == run_id)
         .options(selectinload(EmailResult.reviews)))
    if category:
        q = q.where(EmailResult.category == category)
    if status:
        q = q.where(EmailResult.status == status)
    if state:
        # `state` filters the operator-facing case state, which splits
        # UNREADABLE out of NEEDS_REVIEW. Expressed in SQL rather than in
        # Python so paging and counting stay correct.
        unreadable = EmailResult.review_reason.in_(EmailResult.UNREADABLE_REASONS)
        # coalesce, because `review_reason` is NULL on most rows and SQL
        # three-valued logic makes NOT(FALSE OR NULL) evaluate to NULL --
        # which silently excluded every row the negation was meant to keep.
        missing = sa_or(
            EmailResult.awaiting_document.is_(True),
            func.coalesce(EmailResult.review_reason, "") == "missing_attachment",
        )
        wrong = func.coalesce(EmailResult.review_reason, "") == "wrong_doc_type"
        if state == "WRONG_DOCUMENT":
            q = q.where(wrong)
        elif state == "MISSING_ATTACHMENT":
            q = q.where(~wrong, missing)
        elif state == "UNREADABLE":
            q = q.where(~wrong, ~missing,
                        EmailResult.status == "NEEDS_REVIEW", unreadable)
        elif state == "NEEDS_REVIEW":
            q = q.where(~wrong, ~missing,
                        EmailResult.status == "NEEDS_REVIEW", ~unreadable)
        else:
            q = q.where(~wrong, ~missing, EmailResult.status == state)
    if unreviewed:
        q = q.where(~EmailResult.reviews.any())
    if reviewed:
        q = q.where(EmailResult.reviews.any())
    # Lowest confidence first: the cases a human can most usefully spend
    # attention on sit at the top of the queue.
    q = q.order_by(EmailResult.confidence.is_(None), EmailResult.confidence,
                   EmailResult.email_id).limit(limit)
    return db.scalars(q).all()


# Served inline where a browser can render it, downloaded otherwise.
CONTENT_TYPES = {
    "txt": ("text/plain; charset=utf-8", True),
    "pdf": ("application/pdf", True),
    "png": ("image/png", True),
    "jpg": ("image/jpeg", True),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", False),
    "xlsm": ("application/vnd.ms-excel.sheet.macroEnabled.12", False),
    "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", False),
}


@router.get("/{run_id}/emails/{email_id}/attachments/{index}")
def get_attachment(run_id: int, email_id: str, index: int,
                   db: Session = Depends(get_db)):
    """Serve one of an email's attachments.

    Addressed by position in the email's own attachment list rather than by
    path. The client never names a file, so no request can reach outside the
    dataset however it is crafted.
    """
    row = db.scalar(select(EmailResult).where(
        EmailResult.run_id == run_id, EmailResult.email_id == email_id))
    if not row:
        raise HTTPException(404, "email not found in this run")

    attachments = row.attachments or []
    if not 0 <= index < len(attachments):
        raise HTTPException(404, "no attachment at that position")

    path = attachments[index]
    run = db.get(Run, run_id)
    try:
        data = Inbox(run.source).read_bytes(path)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, f"attachment could not be read: {exc}") from exc

    name = path.rsplit("/", 1)[-1]
    media, inline = CONTENT_TYPES.get(name.rsplit(".", 1)[-1].lower(),
                                      ("application/octet-stream", False))
    disposition = "inline" if inline else "attachment"
    return Response(content=data, media_type=media, headers={
        "Content-Disposition": f'{disposition}; filename="{name}"',
        # The dataset is fixed for a run, so this is safe to cache hard.
        "Cache-Control": "private, max-age=3600",
    })


DOC_NAME = {"SI": "Shipping Instruction", "BL": "draft Bill of Lading"}


def _missing_documents(row):
    """Which of the SI / BL the case still needs."""
    have = {"SI" if "_SI" in a else "BL" if "_BL" in a else None
            for a in (row.attachments or [])}
    return [name for slot, name in DOC_NAME.items() if slot not in have]


def _received_documents(row, source):
    """What the attachments actually turned out to be.

    Read live rather than stored: the point of this panel is to tell the
    sender what they sent us, and the files are the only honest source for
    that. A wrong-document case has at most two small attachments, so the
    cost is trivial and only paid when the panel is opened.
    """
    from ..pipeline.extract import detect_doc_type
    from ..pipeline.extractors import Unreadable, read

    inbox = Inbox(source)
    received, wrong_slots = [], []
    for path in row.attachments or []:
        slot = "SI" if "_SI" in path else "BL" if "_BL" in path else None
        try:
            _fields, text, _meta = read(inbox.read_bytes(path), path)
        except (Unreadable, OSError, Exception):
            continue
        kind = detect_doc_type(text)
        if kind in ("SI", "BL"):
            continue                      # this one is what it should be
        received.append(kind.title())
        if slot:
            wrong_slots.append(DOC_NAME[slot])
    return received, wrong_slots


@router.get("/{run_id}/emails/{email_id}/draft-reply")
def draft_reply(run_id: int, email_id: str, db: Session = Depends(get_db)):
    """Compose a reply asking for the documents this case is missing.

    Returned as text for the operator to copy, not sent. Drafting is cheap
    and reversible; sending on someone's behalf is neither, and the person
    who owns the mailbox should be the one who presses send.
    """
    row = db.scalar(select(EmailResult).where(
        EmailResult.run_id == run_id, EmailResult.email_id == email_id))
    if not row:
        raise HTTPException(404, "email not found in this run")

    run = db.get(Run, run_id)
    ref = row.shipment_reference
    sender = (row.sender or "").split("@")[0].replace(".", " ").replace("_", " ").title()

    wrong_doc = row.review_reason == "wrong_doc_type"
    received, wrong_slots = (_received_documents(row, run.source)
                             if wrong_doc else ([], []))
    missing = wrong_slots if wrong_doc and wrong_slots else _missing_documents(row)
    wanted = " and the ".join(missing) if missing else "the outstanding documents"
    subject = row.subject or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    plural = len(missing) > 1
    it = "them" if plural else "it"

    if wrong_doc and received:
        # Name what they actually sent. "Please resend" without saying what
        # arrived invites them to send the same file again.
        got = " and a ".join(received)
        situation = (f"We received a {got}"
                     + (f" for {ref}" if ref else "")
                     + f", but we still need the {wanted} to complete this "
                     f"check. Could you send {it} separately?")
    else:
        situation = (f"We do not have the {wanted} on file for this "
                     f"shipment, so we cannot complete the document check "
                     f"yet. Could you send {it} across when you have a moment?")

    paragraphs = [
        f"Hi {sender}," if sender else "Hi,",
        "Thanks for your message" + (f" regarding {ref}" if ref else "") + ".",
        situation,
        (f"Once {'they arrive' if plural else 'it arrives'} we will check "
         f"the details and come back to you."),
        "Many thanks,\nShipping Documentation",
    ]
    return {"to": row.sender, "subject": subject,
            "body": "\n\n".join(paragraphs),
            "reference": ref, "missing": missing, "received": received}


@router.post("/{run_id}/emails/{email_id}/retry", response_model=EmailDetail)
def retry_email(run_id: int, email_id: str, db: Session = Depends(get_db),
                user=RequireUser):
    """Re-run extraction and comparison for one email.

    Extraction failures are often transient or fixable -- a re-uploaded
    attachment, an OCR engine that was unavailable on the first pass. A
    per-email retry means one bad document does not require reprocessing
    the whole inbox, and any human decision already recorded is left alone.
    """
    row = db.scalar(select(EmailResult).where(
        EmailResult.run_id == run_id, EmailResult.email_id == email_id)
        .options(selectinload(EmailResult.reviews)))
    if not row:
        raise HTTPException(404, "email not found in this run")

    run = db.get(Run, run_id)
    inbox = Inbox(run.source)
    email = {"email_id": row.email_id, "from": row.sender, "subject": row.subject,
             "body": row.body, "attachments": row.attachments}

    category, result, _decided_by, reason = process(email, inbox)
    row.category = category
    row.route_reason = reason
    row.status = result.status
    row.review_reason = result.review_reason
    row.defect_fields = result.defect_fields
    row.confidence = getattr(result, "confidence", None)
    row.awaiting_document = awaiting_document(email)
    row.shipment_reference = shipment_reference(email)
    row.field_verdicts = [v.to_dict() for v in getattr(result, "verdicts", [])]
    row.notes = result.notes
    db.commit()
    db.refresh(row)
    return row


@router.get("/{run_id}/emails/{email_id}", response_model=EmailDetail)
def get_email(run_id: int, email_id: str, db: Session = Depends(get_db)):
    row = db.scalar(select(EmailResult).where(
        EmailResult.run_id == run_id, EmailResult.email_id == email_id)
        .options(selectinload(EmailResult.reviews)))
    if not row:
        raise HTTPException(404, "email not found in this run")
    return row


@router.get("/{run_id}/submission")
def submission(run_id: int, db: Session = Depends(get_db)):
    """The scored artefact, with any human decision applied over the machine's.

    A reviewer's correction is the better answer, so the exported submission
    reflects it.
    """
    rows = db.scalars(select(EmailResult).where(EmailResult.run_id == run_id)
                      .options(selectinload(EmailResult.reviews))).all()
    out = {}
    for r in rows:
        status, defects = r.status, r.defect_fields
        if r.review:
            status, defects = r.review.final_status, r.review.final_defect_fields
        # Field order matches sample_submission.json exactly, so a diff of
        # the two files shows only values.
        out[r.email_id] = {
            "category": r.category,
            "status": status,
            "review_reason": r.review_reason if status == "NEEDS_REVIEW" else None,
            "defect_fields": defects if status == "MISMATCH" else [],
            "has_defect": status == "MISMATCH",
        }
    return out
