"""Demo affordances.

The deployed app has no login, so everyone who opens it shares one
workspace. A judge arriving after someone else has been triaging sees that
person's decisions already applied, which is confusing at best and reads as
a bug at worst. This gives them one action to get back to a known state.

Kept in its own module, deliberately: it exists for an unauthenticated demo
and is the first thing that should be deleted if this ever carries real
work. Nothing else imports it.
"""
import logging

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..auth import RequireUser
from ..config import settings
from ..db import get_db
from ..models import Review, Run
from ..schemas import RunOut
from .runs import _execute

router = APIRouter(prefix="/api/demo", tags=["demo"])
log = logging.getLogger("sdoc.demo")


@router.post("/reset", response_model=RunOut, status_code=202)
def reset_demo(background: BackgroundTasks, db: Session = Depends(get_db),
               user=RequireUser):
    """Clear every review decision, then start a fresh run.

    Rows only. The `reviews` table and its relationships are untouched, so
    the append-only audit design still holds -- this empties the log, it
    does not remove it.

    Order matters, and not obviously. `_execute` carries standing decisions
    forward onto the run it creates, which is what stops "Run again" from
    discarding an operator's work. Clear the reviews *after* starting the
    run and that same mechanism would faithfully restore everything the
    reset was meant to remove.
    """
    cleared = db.execute(delete(Review)).rowcount
    db.commit()

    remaining = db.scalar(select(func.count()).select_from(Review))
    log.info("demo reset: cleared %s review decision(s), %s remain",
             cleared, remaining)

    run = Run(source=settings.data_source, status="running")
    db.add(run)
    db.commit()
    db.refresh(run)
    background.add_task(_execute, run.id, run.source)
    return run
