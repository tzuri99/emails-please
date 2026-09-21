"""The decision model that replaced "do you agree?".

The old API asked for a decision (confirmed / overridden) *and* an outcome.
Two different requests then produced the same result whenever the machine
had already proposed that outcome, which is what made "Agree with the
machine" and "Mark clear" indistinguishable in the UI.

Now the client states only the outcome and the server derives agreement, so
the history cannot be mislabelled by the caller.
"""
import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import EmailResult, Run


@pytest.fixture
def seeded():
    """One run holding a single flagged email, torn down afterwards."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    run = Run(source="test", status="succeeded", total_emails=1)
    db.add(run)
    db.flush()
    db.add(EmailResult(
        run_id=run.id, email_id="email_test", sender="a@b.c", subject="s", body="b",
        attachments=[], category="BL_COMPARISON", route_reason="test",
        status="MISMATCH", defect_fields=["consignee"], confidence=0.98,
        field_verdicts=[], notes=[],
    ))
    db.commit()
    rid = run.id
    db.close()

    with TestClient(app) as c:
        yield c, rid

    db = SessionLocal()
    db.delete(db.get(Run, rid))
    db.commit()
    db.close()


def test_matching_the_machine_records_agreement(seeded):
    c, rid = seeded
    r = c.post(f"/api/runs/{rid}/emails/email_test/review",
               json={"final_status": "MISMATCH", "final_defect_fields": ["consignee"]})
    assert r.status_code == 200
    assert r.json()["review"]["decision"] == "agreed"


def test_a_different_outcome_is_an_override(seeded):
    c, rid = seeded
    r = c.post(f"/api/runs/{rid}/emails/email_test/review", json={"final_status": "OK"})
    assert r.json()["review"]["decision"] == "overridden"
    assert r.json()["review"]["final_defect_fields"] == []


def test_same_status_different_fields_is_an_override(seeded):
    """Narrowing which fields are wrong is still a correction."""
    c, rid = seeded
    r = c.post(f"/api/runs/{rid}/emails/email_test/review",
               json={"final_status": "MISMATCH", "final_defect_fields": ["shipper"]})
    assert r.json()["review"]["decision"] == "overridden"


def test_a_discrepancy_must_name_fields(seeded):
    """A defect the report cannot describe is not a usable defect."""
    c, rid = seeded
    r = c.post(f"/api/runs/{rid}/emails/email_test/review",
               json={"final_status": "MISMATCH", "final_defect_fields": []})
    assert r.status_code == 422


def test_history_is_appended_never_replaced(seeded):
    c, rid = seeded
    for body in ({"final_status": "OK"},
                 {"final_status": "MISMATCH", "final_defect_fields": ["consignee"]},
                 {"final_status": "NEEDS_REVIEW"}):
        c.post(f"/api/runs/{rid}/emails/email_test/review", json=body)

    d = c.get(f"/api/runs/{rid}/emails/email_test").json()
    assert len(d["reviews"]) == 3, "each decision must leave a record"
    assert [r["final_status"] for r in d["reviews"]] == ["OK", "MISMATCH", "NEEDS_REVIEW"]
    assert d["review"]["final_status"] == "NEEDS_REVIEW", "the latest decision stands"


def test_the_machine_verdict_is_captured_on_each_entry(seeded):
    """Stored per entry so the log stays readable after a re-run changes
    what the machine says."""
    c, rid = seeded
    c.post(f"/api/runs/{rid}/emails/email_test/review", json={"final_status": "OK"})
    d = c.get(f"/api/runs/{rid}/emails/email_test").json()
    assert d["reviews"][0]["machine_status"] == "MISMATCH"


def test_undo_removes_only_the_latest(seeded):
    c, rid = seeded
    c.post(f"/api/runs/{rid}/emails/email_test/review", json={"final_status": "OK"})
    c.post(f"/api/runs/{rid}/emails/email_test/review", json={"final_status": "NEEDS_REVIEW"})
    c.delete(f"/api/runs/{rid}/emails/email_test/review")

    d = c.get(f"/api/runs/{rid}/emails/email_test").json()
    assert len(d["reviews"]) == 1, "undo is a correction, not an erasure"
    assert d["review"]["final_status"] == "OK"


def test_submission_follows_the_standing_decision(seeded):
    c, rid = seeded
    c.post(f"/api/runs/{rid}/emails/email_test/review", json={"final_status": "OK"})
    sub = c.get(f"/api/runs/{rid}/submission").json()
    assert sub["email_test"]["status"] == "OK"
    assert sub["email_test"]["has_defect"] is False
    assert sub["email_test"]["defect_fields"] == []
