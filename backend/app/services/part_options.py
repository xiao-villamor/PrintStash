"""Part Group invariants and atomic replacement for one Model."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import func
from sqlmodel import Session, delete, select

from app.db.models import (
    File,
    FileType,
    Model,
    PartGroup,
    PartOption,
)
from app.db.scopes import live
from app.schemas.models import (
    PartGroupRead,
    PartGroupWrite,
    PartModelRead,
    PartOptionRead,
)

_SOURCE_TYPES = frozenset(
    {FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP}
)


@dataclass(frozen=True)
class PartOptionsError(ValueError):
    code: str


@dataclass(frozen=True)
class _NormalizedOption:
    file_id: int | None
    model_id: int | None
    name: str
    name_key: str
    is_default: bool


def _name(value: str) -> tuple[str, str]:
    display = " ".join(value.split())
    return display, display.casefold()


def read_for_model(
    session: Session,
    model_id: int,
    *,
    thumbnail_url_for: Callable[[Model], str | None] | None = None,
) -> list[PartGroupRead]:
    rows = session.exec(
        select(PartGroup, PartOption)
        .join(PartOption, PartOption.part_group_id == PartGroup.id)
        .where(PartGroup.model_id == model_id)
        .order_by(PartGroup.sort_order.asc(), PartOption.sort_order.asc())  # type: ignore[attr-defined]
    ).all()
    legacy_file_ids = {
        option.file_id for _group, option in rows if option.file_id is not None
    }
    legacy_files = {
        row.id: row
        for row in (
            session.exec(select(File).where(File.id.in_(legacy_file_ids))).all()
            if legacy_file_ids
            else []
        )
    }
    option_model_ids = {
        option.model_id
        if option.model_id is not None
        else legacy_files[option.file_id].model_id
        for _group, option in rows
        if option.model_id is not None or option.file_id in legacy_files
    }
    models = {
        row.id: row
        for row in (
            session.exec(
                select(Model).where(Model.id.in_(option_model_ids), live(Model))
            ).all()
            if option_model_ids
            else []
        )
    }
    file_counts: dict[int, tuple[int, int]] = defaultdict(lambda: (0, 0))
    if option_model_ids:
        for member_id, file_type, count in session.exec(
            select(File.model_id, File.file_type, func.count(File.id))
            .where(File.model_id.in_(option_model_ids), live(File))  # type: ignore[union-attr]
            .group_by(File.model_id, File.file_type)
        ).all():
            source_count, gcode_count = file_counts[int(member_id)]
            if file_type == FileType.GCODE:
                gcode_count += int(count)
            else:
                source_count += int(count)
            file_counts[int(member_id)] = (source_count, gcode_count)

    groups: dict[int, PartGroupRead] = {}
    incomplete: set[int] = set()
    for group, option in rows:
        assert group.id is not None and option.id is not None
        legacy_file = legacy_files.get(option.file_id)
        member_id = option.model_id or (
            legacy_file.model_id if legacy_file is not None else None
        )
        member = models.get(member_id)
        if member is None or (
            legacy_file is not None and legacy_file.deleted_at is not None
        ):
            incomplete.add(group.id)
            continue
        assert member.id is not None
        source_count, gcode_count = file_counts[member.id]
        current = groups.setdefault(
            group.id,
            PartGroupRead(id=group.id, name=group.name, options=[]),
        )
        current.options.append(
            PartOptionRead(
                id=option.id,
                file_id=option.file_id,
                model=PartModelRead(
                    id=member.id,
                    name=member.name,
                    slug=member.slug,
                    thumbnail_url=(
                        thumbnail_url_for(member) if thumbnail_url_for else None
                    ),
                    source_file_count=source_count,
                    gcode_revision_count=gcode_count,
                ),
                name=option.name,
                is_default=option.is_default,
            )
        )
    return [
        group
        for group_id, group in groups.items()
        if group_id not in incomplete
        and len(group.options) >= 1
        and sum(option.is_default for option in group.options) == 1
    ]


def _remove_option(session: Session, option: PartOption | None) -> None:
    if option is None:
        return
    group = session.get(PartGroup, option.part_group_id)
    if group is None:
        session.delete(option)
        session.flush()
        return
    siblings = session.exec(
        select(PartOption)
        .where(PartOption.part_group_id == group.id, PartOption.id != option.id)
        .order_by(PartOption.sort_order.asc())  # type: ignore[attr-defined]
    ).all()
    if len(siblings) < 1:
        session.delete(group)
        session.flush()
        return
    session.delete(option)
    if option.is_default:
        # The partial unique index still sees the old default until its DELETE
        # is flushed, so retire it before promoting the replacement.
        session.flush()
        siblings[0].is_default = True
        session.add(siblings[0])
    session.flush()


def remove_file_from_groups(session: Session, file_id: int) -> None:
    """Preserve Part Group invariants before an Artifact is permanently purged."""
    _remove_option(
        session,
        session.exec(select(PartOption).where(PartOption.file_id == file_id)).first(),
    )


def remove_model_from_groups(session: Session, model_id: int) -> None:
    """Detach a member Model before its permanent purge."""
    _remove_option(
        session,
        session.exec(select(PartOption).where(PartOption.model_id == model_id)).first(),
    )


def _member_graph(
    session: Session, *, replacing_model_id: int | None = None
) -> dict[int, set[int]]:
    stmt = (
        select(PartGroup.model_id, PartOption.model_id)
        .join(PartOption, PartOption.part_group_id == PartGroup.id)
        .where(PartOption.model_id.is_not(None))
    )
    if replacing_model_id is not None:
        stmt = stmt.where(PartGroup.model_id != replacing_model_id)
    children: dict[int, set[int]] = defaultdict(set)
    for parent_id, child_id in session.exec(stmt).all():
        if child_id is not None and child_id != parent_id:
            children[int(parent_id)].add(int(child_id))
    return children


def _would_create_cycle(
    children: dict[int, set[int]], model_id: int, member_ids: set[int]
) -> bool:
    graph = {parent_id: set(child_ids) for parent_id, child_ids in children.items()}
    graph[model_id] = {member_id for member_id in member_ids if member_id != model_id}
    pending = list(graph[model_id])
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        if candidate == model_id:
            return True
        if candidate in seen:
            continue
        seen.add(candidate)
        pending.extend(graph.get(candidate, ()))
    return False


def _creates_cycle(session: Session, model_id: int, member_ids: set[int]) -> bool:
    return _would_create_cycle(
        _member_graph(session, replacing_model_id=model_id), model_id, member_ids
    )


def replace_for_model(
    session: Session, model_id: int, requested: list[PartGroupWrite]
) -> list[PartGroupRead]:
    """Validate the complete desired state, then replace it in one transaction."""
    group_keys: set[str] = set()
    file_ids: set[int] = set()
    model_ids: set[int] = set()
    normalized: list[tuple[str, str, list[_NormalizedOption]]] = []
    for group in requested:
        group_name, group_key = _name(group.name)
        if not group_name or group_key in group_keys:
            raise PartOptionsError("part_group_name_duplicate")
        group_keys.add(group_key)
        option_keys: set[str] = set()
        defaults = 0
        options: list[_NormalizedOption] = []
        for option in group.options:
            option_name, option_key = _name(option.name)
            if not option_name or option_key in option_keys:
                raise PartOptionsError("part_option_name_duplicate")
            if option.file_id is not None:
                if option.file_id in file_ids:
                    raise PartOptionsError("artifact_already_part_option")
                file_ids.add(option.file_id)
            if option.model_id is not None:
                if option.model_id in model_ids:
                    raise PartOptionsError("model_already_part_option")
                model_ids.add(option.model_id)
            option_keys.add(option_key)
            defaults += int(option.is_default)
            options.append(
                _NormalizedOption(
                    file_id=option.file_id,
                    model_id=option.model_id,
                    name=option_name,
                    name_key=option_key,
                    is_default=option.is_default,
                )
            )
        if defaults != 1:
            raise PartOptionsError("part_group_default_required")
        normalized.append((group_name, group_key, options))

    files = (
        session.exec(
            select(File).where(
                File.id.in_(file_ids), File.model_id == model_id, live(File)
            )
        ).all()
        if file_ids
        else []
    )
    if len(files) != len(file_ids):
        raise PartOptionsError("part_option_artifact_not_found")
    if any(file.file_type not in _SOURCE_TYPES for file in files):
        raise PartOptionsError("part_option_artifact_not_source")

    member_models = (
        session.exec(select(Model).where(Model.id.in_(model_ids), live(Model))).all()
        if model_ids
        else []
    )
    if len(member_models) != len(model_ids):
        raise PartOptionsError("part_option_model_not_found")
    conflicts = (
        session.exec(
            select(PartOption.model_id)
            .join(PartGroup, PartGroup.id == PartOption.part_group_id)
            .where(
                PartOption.model_id.in_(model_ids),  # type: ignore[union-attr]
                PartGroup.model_id != model_id,
            )
        ).all()
        if model_ids
        else []
    )
    if conflicts:
        raise PartOptionsError("model_already_part_option")
    if _creates_cycle(session, model_id, model_ids):
        raise PartOptionsError("part_option_cycle")

    existing_group_ids = select(PartGroup.id).where(PartGroup.model_id == model_id)
    session.exec(
        delete(PartOption).where(PartOption.part_group_id.in_(existing_group_ids))
    )
    session.exec(delete(PartGroup).where(PartGroup.model_id == model_id))
    session.flush()

    for group_order, (group_name, group_key, options) in enumerate(normalized):
        group = PartGroup(
            model_id=model_id,
            name=group_name,
            name_key=group_key,
            sort_order=group_order,
        )
        session.add(group)
        session.flush()
        assert group.id is not None
        for option_order, option in enumerate(options):
            session.add(
                PartOption(
                    part_group_id=group.id,
                    file_id=option.file_id,
                    model_id=option.model_id,
                    name=option.name,
                    name_key=option.name_key,
                    sort_order=option_order,
                    is_default=option.is_default,
                )
            )
    session.commit()
    return read_for_model(session, model_id)
