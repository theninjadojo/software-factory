from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Any


class JobStatus(StrEnum):
    QUEUED = "queued"
    DONE = "done"
    FAILED = "failed"  # gave up after max_attempts


@dataclass(frozen=True)
class Job:
    id: int
    kind: str
    payload: dict[str, Any]
    attempts: int  # attempts that failed so far
    max_attempts: int


def backoff(attempts: int, base: float = 2.0, cap: timedelta = timedelta(hours=1)) -> timedelta:
    """How long to wait before the next try after `attempts` failures: 2s, 4s, 8s, ... up to an hour."""
    return timedelta(seconds=min(base**attempts, cap.total_seconds()))
