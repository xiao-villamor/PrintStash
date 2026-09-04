"""repair duplicate Bambu external jobs and preserve capture error detail

Revision ID: f8a6c2d9e1b4
Revises: e7b4c1d9a6f2
Create Date: 2026-08-24 00:00:00
"""

from __future__ import annotations

from datetime import datetime, timezone

import sqlalchemy as sa

from alembic import op

revision: str = "f8a6c2d9e1b4"
down_revision: str | None = "e7b4c1d9a6f2"
branch_labels = None
depends_on = None

_IDENTITY_COLUMNS = (
    "external_task_id",
    "external_subtask_id",
    "external_project_id",
)
_METADATA_COLUMNS = (
    "external_display_name",
    "external_task_id",
    "external_subtask_id",
    "external_project_id",
    "external_profile_id",
    "external_gcode_file",
    "external_plate_index",
    "external_current_layer",
    "external_total_layers",
    "external_nozzle_diameter",
)
_EVIDENCE_RANK = {
    "vault": 0,
    "metadata_only": 1,
    "capture_pending": 2,
    "capture_failed": 2,
    "gcode_archived": 3,
    "project_archived": 4,
}
_TRANSITION_FIELDS = {
    frozenset(("external_project_id", "external_task_id")),
    frozenset(("external_project_id", "external_subtask_id")),
}
_MISSING_TIMESTAMP = datetime.min.replace(tzinfo=timezone.utc)
_OPEN_TIMESTAMP = datetime.max.replace(tzinfo=timezone.utc)


def _timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
        except ValueError:
            pass
    return _MISSING_TIMESTAMP


def _identity(row: sa.RowMapping) -> set[str]:
    return {
        str(row[column]).strip()
        for column in _IDENTITY_COLUMNS
        if row[column] not in (None, "")
    }


def _identity_fields(row: sa.RowMapping) -> dict[str, str]:
    return {
        column: str(row[column]).strip()
        for column in _IDENTITY_COLUMNS
        if row[column] not in (None, "")
    }


def _identity_tokens(row: sa.RowMapping) -> set[tuple[object, str, str]]:
    """Return printer-scoped, typed identity tokens.

    The value alone is not an identity: Bambu identifiers are only meaningful
    on their printer, and a task id must never match a project id merely
    because their serialized values happen to be equal.
    """

    printer_id = row["printer_id"]
    return {
        (printer_id, column, value)
        for column, value in _identity_fields(row).items()
    }


def _lifecycle_overlaps(left: sa.RowMapping, right: sa.RowMapping) -> bool:
    """Prove two rows belong to an overlapping print lifecycle.

    ``created_at`` is deliberately not a lifecycle signal: import timing can
    differ from the printer's start/finish timestamps. An absent start cannot
    prove continuity, while an absent finish is an open active interval.
    """

    left_started = _timestamp(left["started_at"])
    right_started = _timestamp(right["started_at"])
    if left_started == _MISSING_TIMESTAMP or right_started == _MISSING_TIMESTAMP:
        return False
    left_finished = _timestamp(left["finished_at"])
    right_finished = _timestamp(right["finished_at"])
    if left["finished_at"] in (None, ""):
        left_finished = _OPEN_TIMESTAMP
    if right["finished_at"] in (None, ""):
        right_finished = _OPEN_TIMESTAMP
    return max(left_started, right_started) <= min(left_finished, right_finished)


def _strict_transition(left: sa.RowMapping, right: sa.RowMapping) -> bool:
    """Prove a project/task hand-off is one active lifecycle, not a reprint."""

    left_fields = _identity_fields(left)
    right_fields = _identity_fields(right)
    if len(left_fields) != 1 or len(right_fields) != 1:
        return False
    field_pair = frozenset((next(iter(left_fields)), next(iter(right_fields))))
    if field_pair not in _TRANSITION_FIELDS:
        return False
    if left["remote_filename"] != right["remote_filename"]:
        return False
    return _lifecycle_overlaps(left, right)


def _same_identity_or_transition(left: sa.RowMapping, right: sa.RowMapping) -> bool:
    if left["printer_id"] != right["printer_id"]:
        return False
    left_tokens = _identity_tokens(left)
    right_tokens = _identity_tokens(right)
    if left_tokens.intersection(right_tokens):
        # Shared identity alone is not enough: the same project/task can be
        # reused for later reprints. Keep the filename and lifecycle evidence
        # in the proof, and refuse ambiguous rows rather than guessing.
        return (
            left["remote_filename"] == right["remote_filename"]
            and _lifecycle_overlaps(left, right)
        )
    # A project-only -> task-only hand-off is safe only when its lifecycle
    # intervals overlap and each row has exactly one non-conflicting identity.
    # Filename/time alone is deliberately insufficient: fast reprints and
    # transitive identity chains must remain separate history rows.
    return bool(left_tokens and right_tokens) and _strict_transition(left, right)


def _groups(rows: list[sa.RowMapping]) -> list[list[sa.RowMapping]]:
    # Select disjoint groups by one shared, printer-scoped identity token. A
    # union-find would incorrectly collapse A(project), B(project+task),
    # C(task) transitively, and a raw value key would cross printers/types.
    candidates: list[tuple[int, tuple[object, str, str], list[int]]] = []
    token_rows: dict[tuple[object, str, str], list[int]] = {}
    for index, row in enumerate(rows):
        for token in _identity_tokens(row):
            token_rows.setdefault(token, []).append(index)
    for token, indexes in token_rows.items():
        if len(indexes) > 1:
            candidates.append((min(indexes), token, indexes))
    used: set[int] = set()
    groups: list[list[sa.RowMapping]] = []
    for _first, _token, indexes in sorted(candidates):
        available = [index for index in indexes if index not in used]
        # Only consume a clique of lifecycle-compatible rows. This prevents a
        # chain of pairwise overlaps from turning into one transitive row.
        while len(available) > 1:
            anchor, *rest = available
            group = [anchor]
            for candidate in rest:
                if all(
                    _same_identity_or_transition(rows[member], rows[candidate])
                    for member in group
                ):
                    group.append(candidate)
            if len(group) == 1:
                available = rest
                continue
            used.update(group)
            groups.append([rows[index] for index in group])
            available = [index for index in rest if index not in group]

    # Resolve only demonstrable project/task hand-offs among rows not already
    # grouped by an explicit shared identity, and consume each row once.
    remaining = [index for index in range(len(rows)) if index not in used]
    for position, left_index in enumerate(remaining):
        if left_index in used:
            continue
        for right_index in remaining[position + 1 :]:
            if right_index not in used and _same_identity_or_transition(
                rows[left_index], rows[right_index]
            ):
                used.update((left_index, right_index))
                groups.append([rows[left_index], rows[right_index]])
                break
    return groups


def upgrade() -> None:
    with op.batch_alter_table("print_jobs") as batch:
        batch.add_column(sa.Column("artifact_capture_error_code", sa.String(128)))
        batch.add_column(sa.Column("artifact_capture_error_message", sa.String(1024)))
        batch.add_column(sa.Column("dedupe_absorbed_at", sa.DateTime()))
        batch.add_column(sa.Column("dedupe_survivor_id", sa.Integer()))
        batch.create_index("ix_print_jobs_dedupe_absorbed_at", ["dedupe_absorbed_at"])
        batch.create_index("ix_print_jobs_dedupe_survivor_id", ["dedupe_survivor_id"])

    connection = op.get_bind()
    rows = list(
        connection.execute(
            sa.text(
                "SELECT id, printer_id, file_id, model_id, remote_filename, state, "
                "progress, provider_job_id, source, external_display_name, "
                "external_task_id, external_subtask_id, external_project_id, "
                "external_profile_id, external_gcode_file, external_plate_index, "
                "external_current_layer, external_total_layers, "
                "external_nozzle_diameter, artifact_evidence, artifact_capture_error, "
                "artifact_capture_error_code, artifact_capture_error_message, "
                "started_at, finished_at, created_at, updated_at "
                "FROM print_jobs WHERE source = 'external' "
                "AND (external_task_id IS NOT NULL OR external_subtask_id IS NOT NULL "
                "OR external_project_id IS NOT NULL) "
                "ORDER BY printer_id, created_at, id"
            )
        ).mappings()
    )
    now = datetime.now(timezone.utc)
    for group in _groups(rows):
        ordered = sorted(group, key=lambda row: (_timestamp(row["created_at"]), row["id"]))
        survivor = ordered[0]
        survivor_id = int(survivor["id"])
        values: dict[str, object] = {}
        for column in _METADATA_COLUMNS:
            values[column] = next(
                (
                    row[column]
                    for row in ordered
                    if row[column] not in (None, "")
                ),
                None,
            )
        provider_job_id = next(
            (
                row["provider_job_id"]
                for row in reversed(ordered)
                if row["provider_job_id"] not in (None, "")
            ),
            None,
        )
        best_evidence = max(
            ordered,
            key=lambda row: _EVIDENCE_RANK.get(str(row["artifact_evidence"]), 0),
        )
        values.update(
            provider_job_id=provider_job_id,
            artifact_evidence=best_evidence["artifact_evidence"],
            artifact_capture_error=best_evidence["artifact_capture_error"],
            artifact_capture_error_code=best_evidence["artifact_capture_error_code"],
            artifact_capture_error_message=best_evidence[
                "artifact_capture_error_message"
            ],
        )
        if _EVIDENCE_RANK.get(str(best_evidence["artifact_evidence"]), 0) >= 3:
            values["file_id"] = best_evidence["file_id"]
            values["model_id"] = best_evidence["model_id"]
        latest = ordered[-1]
        for column in ("state", "progress", "started_at", "finished_at", "updated_at"):
            values[column] = latest[column]
        values["updated_at"] = max(_timestamp(latest["updated_at"]), now)
        connection.execute(
            sa.text(
                "UPDATE print_jobs SET "
                + ", ".join(f"{column} = :{column}" for column in values)
                + " WHERE id = :id"
            ),
            {**values, "id": survivor_id},
        )
        for absorbed in ordered[1:]:
            connection.execute(
                sa.text(
                    "UPDATE print_jobs SET dedupe_absorbed_at = :absorbed_at, "
                    "dedupe_survivor_id = :survivor_id, updated_at = :updated_at "
                    "WHERE id = :id"
                ),
                {
                    "absorbed_at": now,
                    "survivor_id": survivor_id,
                    "updated_at": now,
                    "id": int(absorbed["id"]),
                },
            )


def downgrade() -> None:
    connection = op.get_bind()
    # Dropping these columns would erase absorbed-row lineage and actionable
    # capture diagnostics. Refuse before any schema mutation when data exists;
    # a clean, never-used migration can still be downgraded safely.
    existing = connection.execute(
        sa.text(
            "SELECT 1 FROM print_jobs WHERE dedupe_absorbed_at IS NOT NULL "
            "OR dedupe_survivor_id IS NOT NULL "
            "OR artifact_capture_error_code IS NOT NULL "
            "OR artifact_capture_error_message IS NOT NULL LIMIT 1"
        )
    ).first()
    if existing is not None:
        raise RuntimeError(
            "cannot downgrade Bambu identity repair after lineage or capture data exists"
        )
    with op.batch_alter_table("print_jobs") as batch:
        batch.drop_index("ix_print_jobs_dedupe_survivor_id")
        batch.drop_index("ix_print_jobs_dedupe_absorbed_at")
        batch.drop_column("dedupe_survivor_id")
        batch.drop_column("dedupe_absorbed_at")
        batch.drop_column("artifact_capture_error_message")
        batch.drop_column("artifact_capture_error_code")
