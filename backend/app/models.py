"""Persisted pipeline output and the human decisions layered on top.

A Run is one pass over the inbox. Its EmailResults are immutable machine
output; a Review is a person's decision about one of them. Keeping the two
apart means a re-run never destroys review history, and the report can always
show what the machine said next to what the human concluded.
"""
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Integer,
                        String, Text, UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now():
    return datetime.now(timezone.utc)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default="running")
    error: Mapped[str | None] = mapped_column(Text)
    total_emails: Mapped[int] = mapped_column(Integer, default=0)

    results: Mapped[list["EmailResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan")


class EmailResult(Base):
    """One email's machine verdict. Immutable once written."""
    __tablename__ = "email_results"
    __table_args__ = (UniqueConstraint("run_id", "email_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[str] = mapped_column(String(64), index=True)

    sender: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    body: Mapped[str] = mapped_column(Text, default="")
    attachments: Mapped[list] = mapped_column(JSON, default=list)

    category: Mapped[str] = mapped_column(String(32), index=True)
    route_reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), index=True)
    review_reason: Mapped[str | None] = mapped_column(String(32))
    defect_fields: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float | None] = mapped_column(Float)

    # The sender is asking us for a document we do not hold. Distinct from
    # a document that arrived and would not parse: the useful action is to
    # reply, not to retry.
    awaiting_document: Mapped[bool] = mapped_column(Boolean, default=False)
    shipment_reference: Mapped[str | None] = mapped_column(String(64))

    # Full per-field traces: raw values, every normalisation step, rationale.
    field_verdicts: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[list] = mapped_column(JSON, default=list)

    run: Mapped[Run] = relationship(back_populates="results")
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="result", cascade="all, delete-orphan",
        order_by="Review.created_at")

    @property
    def review(self):
        """The decision that stands: the most recent one."""
        return self.reviews[-1] if self.reviews else None

    @property
    def has_defect(self):
        return self.status == "MISMATCH"

    @property
    def review_is_stale(self):
        """The standing decision was made against a different machine verdict.

        Decisions are carried across re-runs, so a reviewer's conclusion
        outlives the run it was recorded in. But if re-extraction changed
        what the machine thinks -- an OCR retry that finally read the
        document, say -- the human was answering a different question, and
        their decision should not be applied as though nothing moved.

        Derived from the verdict captured on the review rather than stored,
        so it cannot drift out of step with the row it describes.
        """
        standing = self.review
        return bool(standing
                    and standing.machine_status
                    and standing.machine_status != self.status)

    # Reasons that mean "the document arrived but we could not read it".
    # `missing_attachment` and `wrong_doc_type` are deliberately NOT here:
    # a document that never arrived, and one where the wrong file was sent,
    # are both answered by replying to the sender, not by retrying a parse.
    UNREADABLE_REASONS = ("unreadable",)

    @property
    def case_state(self):
        """What the operator is being asked to do, which is not the same
        question as what the scorer records.

        `status` stays OK / MISMATCH / NEEDS_REVIEW because that is the
        submission contract. UNREADABLE is split out of NEEDS_REVIEW for the
        queue, because a document that would not parse needs a retry or a
        fresh copy, while a genuine judgement call needs a person to read
        it. Lumping them together sends both to the same place.
        """
        if self.review_reason == "wrong_doc_type":
            return "WRONG_DOCUMENT"
        if self.awaiting_document or self.review_reason == "missing_attachment":
            return "MISSING_ATTACHMENT"
        if self.status == "NEEDS_REVIEW" and self.review_reason in self.UNREADABLE_REASONS:
            return "UNREADABLE"
        return self.status


class Review(Base):
    """A person's decision, appended -- never edited, never overwritten.

    Re-reviewing adds a row rather than replacing one, so the table is the
    audit trail: who decided what, when, and why, in order. The current
    decision is simply the most recent row. An audit log you can edit is not
    an audit log.
    """
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    result_id: Mapped[int] = mapped_column(
        ForeignKey("email_results.id", ondelete="CASCADE"), index=True)

    # agreed (matched the machine) | overridden (differed from it)
    decision: Mapped[str] = mapped_column(String(32))
    # What the machine had proposed, captured so the log stays readable even
    # after a re-run changes the machine's own verdict.
    machine_status: Mapped[str] = mapped_column(String(32), default="")
    final_status: Mapped[str] = mapped_column(String(32))
    final_defect_fields: Mapped[list] = mapped_column(JSON, default=list)
    reviewer: Mapped[str] = mapped_column(String(320), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    seconds_to_decide: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    result: Mapped[EmailResult] = relationship(back_populates="reviews")
