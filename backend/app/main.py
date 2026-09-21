"""SDOC verification API."""
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from . import auth
from .config import settings
from .db import Base, SessionLocal, engine
from .models import Run
from .routers import demo, review, runs

# Uvicorn only attaches handlers to its own loggers, so an application
# logger would propagate to a bare root logger and be dropped below
# WARNING -- progress lines would simply never appear in `az containerapp
# logs`. basicConfig only touches root if it has no handler, so uvicorn's
# own formatting is left alone.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("sdoc")

def _fail_interrupted_runs():
    """Mark runs that were still in flight when the process last died.

    A run executes in THIS process, via BackgroundTasks. So any row still
    "running" when we start up belongs to a process that no longer exists:
    the container was restarted, redeployed, scaled in, or OOM-killed
    part-way through. Nothing will ever finish those rows.

    That matters beyond tidiness. The UI polls the newest run until it
    leaves "running", so one interrupted row makes the app look like it is
    processing forever -- the failure is invisible and indistinguishable
    from a slow database.

    ASSUMES A SINGLE REPLICA, which is how sdoc-api is deployed
    (min=max=1). With more than one, an instance starting up would wrongly
    fail a run another instance is still executing; that needs a worker id
    on the row, not a blanket sweep.
    """
    with SessionLocal() as db:
        stale = db.scalars(select(Run).where(Run.status == "running")).all()
        for run in stale:
            run.status = "failed"
            run.error = ("Interrupted: the API restarted while this run was "
                         "in progress. Start a new run.")
            run.finished_at = datetime.now(timezone.utc)
        if stale:
            db.commit()
            log.warning("marked %s interrupted run(s) as failed: %s",
                        len(stale), [r.id for r in stale])


@asynccontextmanager
async def lifespan(_app):
    # create_all is enough for a single-service deployment; Alembic is in
    # requirements for when the schema needs to change under live data.
    Base.metadata.create_all(bind=engine)
    _fail_interrupted_runs()
    yield


app = FastAPI(
    lifespan=lifespan,
    title="SDOC — Shipping Document Verification",
    description="Inbox triage, SI/BL comparison with confidence scoring, "
                "and human review.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(runs.router)
app.include_router(review.router)
app.include_router(demo.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/config")
def client_config():
    """What the frontend needs to know before it renders.

    Auth is advertised rather than assumed so one build of the UI works
    against both an open demo and a protected deployment.
    """
    return {"auth_required": auth.enabled(),
            "firebase_project_id": settings.firebase_project_id or None}
