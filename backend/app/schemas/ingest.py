from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


JobState = Literal["pending", "running", "completed", "failed"]


class IngestResponse(BaseModel):
    """Returned immediately from POST /ingest/orca."""

    job_id: str
    state: JobState
    message: str = "ingestion queued"


class IngestJobStatus(BaseModel):
    job_id: str
    owner_user_id: Optional[int] = Field(default=None, exclude=True)
    state: JobState
    model_id: Optional[int] = None
    file_id: Optional[int] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    # Progress hints — additive, absent for clients that only know the
    # original state machine.
    step: Optional[int] = None
    total_steps: Optional[int] = None
    label: Optional[str] = None
    progress: Optional[float] = None  # 0–100
    result: Optional[dict[str, Any]] = None
