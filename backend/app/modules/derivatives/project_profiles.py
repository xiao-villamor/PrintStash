"""Read current derived project evidence without parsing original files in HTTP."""

import json

from sqlmodel import Session

from app.db.models import DerivativeKind, File
from app.schemas.jobs import DerivativeStatus
from app.schemas.project_profiles import FileProjectProfileRead

from . import records


def read(session: Session, file: File) -> FileProjectProfileRead:
    current = records.rows_for(session, file)
    status = next(
        value
        for value in records.read(session, file, current_rows=current)
        if value.kind is DerivativeKind.PROJECT_PROFILE
    )
    profile = None
    if status.state is DerivativeStatus.READY:
        row = current[DerivativeKind.PROJECT_PROFILE]
        profile = json.loads(row.output_json)
    return FileProjectProfileRead(
        state=status.state, profile=profile, failure_reason=status.failure_reason
    )
