"""Manufacturing history builders retain quantities and physical-attempt identity."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from app.db.models import (
    Model,
    MultipartBuild,
    MultipartBuildAttempt,
    MultipartBuildConfirmation,
    MultipartBuildPart,
    MultipartModel,
    MultipartPart,
    PrintJob,
)
from tests.factories._support import save


def build_multipart_part(
    session: Session, composition: MultipartModel, name: str = "Leg", **overrides: Any
) -> MultipartPart:
    overrides.setdefault("name_key", name.casefold())
    overrides.setdefault("quantity", 1)
    return save(
        session,
        MultipartPart(multipart_model_id=composition.id, name=name, **overrides),
    )


def build_multipart_build(
    session: Session, composition: MultipartModel, **overrides: Any
) -> MultipartBuild:
    overrides.setdefault("name", "First manufacturing run")
    overrides.setdefault("composition_name", composition.name)
    overrides.setdefault("collection_id", composition.collection_id)
    overrides.setdefault("object_quantity", 1)
    return save(session, MultipartBuild(multipart_model_id=composition.id, **overrides))


def build_multipart_build_part(
    session: Session, build: MultipartBuild, model: Model, **overrides: Any
) -> MultipartBuildPart:
    overrides.setdefault("name", "Leg")
    overrides.setdefault("quantity", 1)
    overrides.setdefault(
        "required_units", overrides["quantity"] * build.object_quantity
    )
    overrides.setdefault(
        "choices_json", json.dumps([{"model_id": model.id, "name": model.name}])
    )
    overrides.setdefault("selected_model_id", model.id)
    return save(session, MultipartBuildPart(build_id=build.id, **overrides))


def build_multipart_build_attempt(
    session: Session, part: MultipartBuildPart, job: PrintJob, **overrides: Any
) -> MultipartBuildAttempt:
    overrides.setdefault("historical_job_id", job.id)
    overrides.setdefault("model_id", job.model_id)
    overrides.setdefault("revision_id", job.file_id)
    overrides.setdefault("planned_units", 1)
    return save(
        session, MultipartBuildAttempt(part_id=part.id, job_id=job.id, **overrides)
    )


def build_multipart_build_confirmation(
    session: Session, attempt: MultipartBuildAttempt, **overrides: Any
) -> MultipartBuildConfirmation:
    overrides.setdefault("idempotency_key", "result-confirmation")
    overrides.setdefault("requested_version", 0)
    overrides.setdefault("valid_units", 0)
    return save(session, MultipartBuildConfirmation(attempt_id=attempt.id, **overrides))
