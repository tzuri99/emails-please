"""A run must always reach a terminal state.

The UI polls the newest run until it stops saying "running". So a row that
never leaves that state is not a cosmetic problem: it makes a crashed run
indistinguishable from a slow one, and the app appears to process forever.

Two ways a run can die without finishing, both covered here:
  - the pipeline raises, and the failure has to be recorded on the row
  - the process is killed outright, so nothing gets to record anything
"""
import pytest

from app.db import Base, SessionLocal, engine
from app.main import _fail_interrupted_runs
from app.models import Run
from app.routers.runs import _execute


@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


def _run(db, status="running"):
    run = Run(source="/nonexistent", status=status)
    db.add(run)
    db.commit()
    db.refresh(run)
    return run.id


def test_a_crashing_run_is_recorded_as_failed(db, monkeypatch):
    """A pipeline that raises leaves the row failed, with the reason on it.

    The inbox is made to blow up rather than the source being bogus: a
    missing directory yields an empty inbox, which is a legitimate
    zero-email run, not a crash.
    """
    def explode(_source):
        raise RuntimeError("inbox is on fire")

    monkeypatch.setattr("app.routers.runs.Inbox", explode)
    run_id = _run(db)

    _execute(run_id, "/data")

    db.expire_all()
    run = db.get(Run, run_id)
    assert run.status == "failed"
    assert "inbox is on fire" in run.error   # diagnosable, not just "failed"
    assert "RuntimeError" in run.error
    assert run.finished_at is not None       # and terminal


def test_a_vanished_run_does_not_raise(db):
    """Deleted between the POST and the background task starting.

    Nothing to record, but the task must not blow up in a thread whose
    exception nobody is waiting on.
    """
    run_id = _run(db)
    db.delete(db.get(Run, run_id))
    db.commit()

    _execute(run_id, "/definitely/not/a/real/inbox")   # must not raise


def test_interrupted_runs_are_swept_on_startup(db):
    """A run left "running" by a killed process is failed at next startup.

    This is the case no exception handler can catch: an OOM kill or a
    container restart means the process is simply gone mid-run.
    """
    orphan = _run(db, status="running")
    done = _run(db, status="succeeded")

    _fail_interrupted_runs()

    db.expire_all()
    assert db.get(Run, orphan).status == "failed"
    assert "restarted" in db.get(Run, orphan).error
    assert db.get(Run, orphan).finished_at is not None
    # A finished run is left exactly as it was.
    assert db.get(Run, done).status == "succeeded"
