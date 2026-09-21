"""A re-run must not throw away the operator's work.

Reviews hang off EmailResult rows and every run builds new ones, so without
carrying decisions forward, pressing "Run again" empties the Reviewed tab
and -- the part that actually costs marks -- makes /submission revert to the
machine's raw verdict, silently dropping every human correction.

Carrying them forward raises its own question: a decision made against a
verdict that has since changed was answering a different question. That is
flagged rather than applied quietly.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.db import Base, SessionLocal, engine
from app.models import EmailResult, Review, Run
from app.routers import runs as runs_module
from app.routers.runs import _execute

EMAIL_ID = "email_carry"


class _FakeInbox:
    def __init__(self, _source):
        pass

    def emails(self):
        return [{"email_id": EMAIL_ID, "from": "a@b.c", "subject": "s",
                 "body": "b", "attachments": []}]


def _verdict(status, fields):
    def _process(_email, _inbox):
        return ("BL_COMPARISON",
                SimpleNamespace(status=status, review_reason=None,
                                defect_fields=fields, confidence=0.9,
                                verdicts=[], notes=[]),
                "machine", "reason")
    return _process


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def reviewed(db):
    """A finished run holding one email a human has already decided."""
    run = Run(source="/data", status="succeeded", total_emails=1)
    db.add(run)
    db.flush()
    row = EmailResult(
        run_id=run.id, email_id=EMAIL_ID, sender="a@b.c", subject="s", body="b",
        attachments=[], category="BL_COMPARISON", route_reason="r",
        status="MISMATCH", defect_fields=["consignee"], confidence=0.9,
        field_verdicts=[], notes=[],
    )
    row.reviews.append(Review(
        decision="overridden", machine_status="MISMATCH",
        final_status="OK", final_defect_fields=[],
        reviewer="ops@example.com", note="checked with the carrier",
        seconds_to_decide=12.0,
        created_at=datetime.now(timezone.utc) - timedelta(hours=1),
    ))
    db.add(row)
    db.commit()
    return row.reviews[-1].created_at


def _rerun(db, monkeypatch, status, fields):
    monkeypatch.setattr(runs_module, "Inbox", _FakeInbox)
    monkeypatch.setattr(runs_module, "process", _verdict(status, fields))
    run = Run(source="/data", status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    _execute(run.id, "/data")
    db.expire_all()
    return db.query(EmailResult).filter_by(run_id=run.id, email_id=EMAIL_ID).one()


def test_the_decision_survives_a_rerun(db, reviewed, monkeypatch):
    row = _rerun(db, monkeypatch, "MISMATCH", ["consignee"])

    assert row.review is not None, "the operator's decision was discarded"
    assert row.review.final_status == "OK"
    assert row.review.reviewer == "ops@example.com"
    assert row.review.note == "checked with the carrier"


def test_the_original_timestamp_is_preserved(db, reviewed, monkeypatch):
    """It is the same decision, not a new one made at re-run time."""
    row = _rerun(db, monkeypatch, "MISMATCH", ["consignee"])

    assert row.review.created_at.replace(tzinfo=timezone.utc) == \
        reviewed.replace(tzinfo=timezone.utc)


def test_an_unchanged_verdict_is_not_flagged(db, reviewed, monkeypatch):
    row = _rerun(db, monkeypatch, "MISMATCH", ["consignee"])

    assert row.review_is_stale is False


def test_a_changed_verdict_flags_the_decision_for_a_recheck(db, reviewed, monkeypatch):
    """Re-extraction now says OK, but the human judged a MISMATCH.

    The decision is kept and marked, not dropped and not applied silently.
    """
    row = _rerun(db, monkeypatch, "OK", [])

    assert row.review is not None
    assert row.review_is_stale is True


def test_the_earlier_run_keeps_its_own_history(db, reviewed, monkeypatch):
    """Carrying forward copies; it does not move or re-parent."""
    _rerun(db, monkeypatch, "MISMATCH", ["consignee"])

    earlier = (db.query(EmailResult)
               .join(Run, EmailResult.run_id == Run.id)
               .filter(Run.status == "succeeded", EmailResult.email_id == EMAIL_ID)
               .order_by(EmailResult.id).first())
    assert earlier.review is not None
