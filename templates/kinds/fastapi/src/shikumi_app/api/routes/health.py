import logging

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from shikumi_app.api.schemas import Status

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


@router.get("/health")
def health() -> Status:
    """Liveness: the process is up. Does not touch the database."""
    return Status(status="ok")


@router.get("/ready", responses={503: {"model": Status}})
def ready(request: Request, response: Response) -> Status:
    """Readiness: the database answers."""
    try:
        with request.app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError:
        log.warning("database not reachable", exc_info=True)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Status(status="database unavailable")
    return Status(status="ok")
