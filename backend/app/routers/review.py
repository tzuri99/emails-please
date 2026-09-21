"""Human-in-the-loop decisions.

The review panel is keyboard-driven, so these endpoints are deliberately
cheap: one round trip per decision, and the response carries the next case
so the client never waits on a second fetch to advance the queue.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..auth import RequireUser
from ..db import get_db
from ..models import EmailResult, Review
from ..schemas import EmailSummary, ReviewIn

router = APIRouter(prefix="/api/runs/{run_id}/emails/{email_id}", tags=["review"])

VALID_STATUS = {"OK", "MISMATCH", "NEEDS_REVIEW"}


def _load(db, run_id, email_id):
    row = db.scalar(select(EmailResult).where(
        EmailResult.run_id == run_id, EmailResult.email_id == email_id)
        .options(selectinload(EmailResult.reviews)))
    if not row:
        raise HTTPException(404, "email not found in this run")
    return row


@router.post("/review", response_model=EmailSummary)
def submit_review(run_id: int, email_id: str, payload: ReviewIn,
                  db: Session = Depends(get_db), user=RequireUser):
    row = _load(db, run_id, email_id)

    # The reviewer states an outcome; whether that agrees with the machine is
    # derived, never asked. Asking produced two controls that did the same
    # thing whenever the machine had already proposed the same outcome.
    if payload.final_status is None:
        final_status, final_fields = row.status, row.defect_fields
    else:
        if payload.final_status not in VALID_STATUS:
            raise HTTPException(422, f"final_status must be one of {sorted(VALID_STATUS)}")
        final_status = payload.final_status
        final_fields = (payload.final_defect_fields
                        if final_status == "MISMATCH" else [])

    if final_status == "MISMATCH" and not final_fields:
        raise HTTPException(422, "a discrepancy must name at least one field")

    agreed = (final_status == row.status
              and set(final_fields) == set(row.defect_fields))

    # The reviewer is whoever the token says they are. A client-supplied
    # name would make the audit trail unfalsifiable in the wrong direction.
    reviewer = user.display if user.verified else (payload.reviewer or "anonymous")

    # Appended, never replaced: the rows are the audit trail.
    db.add(Review(result_id=row.id,
                  decision="agreed" if agreed else "overridden",
                  machine_status=row.status,
                  final_status=final_status, final_defect_fields=final_fields,
                  reviewer=reviewer, note=payload.note,
                  seconds_to_decide=payload.seconds_to_decide))
    db.commit()
    db.refresh(row)
    return row


@router.delete("/review", response_model=EmailSummary)
def clear_review(run_id: int, email_id: str, db: Session = Depends(get_db),
                 user=RequireUser):
    """Withdraw the standing decision.

    Only the latest entry is removed, so earlier history survives: undo is
    a correction, not a way to erase the record.
    """
    row = _load(db, run_id, email_id)
    if row.reviews:
        db.delete(row.reviews[-1])
        db.commit()
        db.refresh(row)
    return row
