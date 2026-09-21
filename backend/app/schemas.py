"""Response shapes. Field traces pass through as JSON already shaped by the
pipeline, so the UI receives exactly what the comparator recorded."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ReviewIn(BaseModel):
    """A reviewer's decision.

    Only the outcome is supplied. Whether it agrees with the machine is
    derived server-side, so the client cannot mislabel its own history.
    """
    final_status: str | None = None    # None means "whatever the machine said"
    final_defect_fields: list[str] = []
    reviewer: str = ""
    note: str = ""
    seconds_to_decide: float | None = None


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    decision: str
    machine_status: str = ""
    final_status: str
    final_defect_fields: list[str]
    reviewer: str
    note: str
    seconds_to_decide: float | None
    created_at: datetime


class EmailSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    email_id: str
    sender: str
    subject: str
    category: str
    status: str
    case_state: str
    review_reason: str | None
    defect_fields: list[str]
    confidence: float | None
    awaiting_document: bool = False
    shipment_reference: str | None = None
    review: ReviewOut | None = None
    # The standing decision predates a change in the machine's verdict, so
    # it needs a second look rather than silent re-application.
    review_is_stale: bool = False


class EmailDetail(EmailSummary):
    # Every decision ever recorded, oldest first -- the audit trail.
    reviews: list[ReviewOut] = []
    body: str
    attachments: list
    route_reason: str
    field_verdicts: list
    notes: list


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    started_at: datetime
    finished_at: datetime | None
    source: str
    status: str
    error: str | None
    total_emails: int
