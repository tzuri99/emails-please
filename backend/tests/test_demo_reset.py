"""Reset must actually leave a clean workspace.

The deployed demo has no login, so a judge inherits whatever the previous
visitor decided. Reset clears the decisions and re-runs.

The interesting case is the interaction with carry-forward: runs copy
standing decisions onto their new rows, so a reset that cleared reviews in
the wrong order would watch the new run put them all straight back.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import EmailResult, Review, Run


@pytest.fixture
def seeded():
    """A finished run with two decisions already recorded."""
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(Review).delete()
    db.query(EmailResult).delete()
    db.query(Run).delete()
    db.commit()

    run = Run(source="/data", status="succeeded", total_emails=2)
    db.add(run)
    db.flush()
    for n in (1, 2):
        row = EmailResult(
            run_id=run.id, email_id=f"email_{n:03d}", sender="a@b.c",
            subject="s", body="b", attachments=[], category="BL_COMPARISON",
            route_reason="r", status="MISMATCH", defect_fields=["consignee"],
            confidence=0.9, field_verdicts=[], notes=[],
        )
        row.reviews.append(Review(
            decision="agreed", machine_status="MISMATCH",
            final_status="MISMATCH", final_defect_fields=["consignee"],
            reviewer="judge-one", note="", created_at=datetime.now(timezone.utc),
        ))
        db.add(row)
    db.commit()
    db.close()

    with TestClient(app) as c:
        yield c

    db = SessionLocal()
    db.query(Review).delete()
    db.query(EmailResult).delete()
    db.query(Run).delete()
    db.commit()
    db.close()


def _reviews():
    db = SessionLocal()
    try:
        return db.query(Review).count()
    finally:
        db.close()


def test_reset_clears_every_decision(seeded):
    assert _reviews() == 2, "fixture should start with decisions recorded"

    response = seeded.post("/api/demo/reset")

    assert response.status_code == 202
    assert _reviews() == 0


def test_reset_starts_a_new_run(seeded):
    before = seeded.get("/api/runs").json()[0]["id"]

    new_id = seeded.post("/api/demo/reset").json()["id"]

    assert new_id > before


def test_carry_forward_does_not_resurrect_cleared_decisions(seeded):
    """The ordering trap: clear first, then run.

    Runs copy standing decisions onto their rows. Were reset to start the
    run before clearing, the run would restore exactly what was deleted and
    the button would appear to do nothing.
    """
    seeded.post("/api/demo/reset")

    # TestClient runs BackgroundTasks to completion before returning, so
    # the new run has finished carrying forward whatever it found.
    assert _reviews() == 0


def test_the_reviews_table_still_exists(seeded):
    """Rows are cleared; the schema is not touched."""
    seeded.post("/api/demo/reset")

    db = SessionLocal()
    try:
        assert db.query(Review).count() == 0   # queryable, therefore present
    finally:
        db.close()
