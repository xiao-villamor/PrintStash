"""Part Group invariants and atomic replacement for one Model."""

from __future__ import annotations

from dataclasses import dataclass

from sqlmodel import Session, delete, select

from app.db.models import File, FileType, PartGroup, PartOption
from app.db.scopes import live
from app.schemas.models import PartGroupRead, PartGroupWrite, PartOptionRead

_SOURCE_TYPES = frozenset(
    {FileType.STL, FileType.THREE_MF, FileType.OBJ, FileType.STEP}
)


@dataclass(frozen=True)
class PartOptionsError(ValueError):
    code: str


def _name(value: str) -> tuple[str, str]:
    display = " ".join(value.split())
    return display, display.casefold()


def read_for_model(session: Session, model_id: int) -> list[PartGroupRead]:
    rows = session.exec(
        select(PartGroup, PartOption, File.deleted_at)
        .join(PartOption, PartOption.part_group_id == PartGroup.id)
        .join(File, File.id == PartOption.file_id)
        .where(PartGroup.model_id == model_id)
        .order_by(PartGroup.sort_order.asc(), PartOption.sort_order.asc())  # type: ignore[attr-defined]
    ).all()
    groups: dict[int, PartGroupRead] = {}
    incomplete: set[int] = set()
    for group, option, deleted_at in rows:
        assert group.id is not None and option.id is not None
        if deleted_at is not None:
            incomplete.add(group.id)
            continue
        current = groups.setdefault(
            group.id,
            PartGroupRead(id=group.id, name=group.name, options=[]),
        )
        current.options.append(
            PartOptionRead(
                id=option.id,
                file_id=option.file_id,
                name=option.name,
                is_default=option.is_default,
            )
        )
    return [
        group
        for group_id, group in groups.items()
        if group_id not in incomplete
        and len(group.options) >= 2
        and sum(option.is_default for option in group.options) == 1
    ]


def remove_file_from_groups(session: Session, file_id: int) -> None:
    """Preserve Part Group invariants before an Artifact is permanently purged."""
    option = session.exec(
        select(PartOption).where(PartOption.file_id == file_id)
    ).first()
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
    if len(siblings) < 2:
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


def replace_for_model(
    session: Session, model_id: int, requested: list[PartGroupWrite]
) -> list[PartGroupRead]:
    """Validate the complete desired state, then replace it in one transaction."""
    group_keys: set[str] = set()
    file_ids: set[int] = set()
    normalized: list[tuple[str, str, list[tuple[int, str, str, bool]]]] = []
    for group in requested:
        group_name, group_key = _name(group.name)
        if not group_name or group_key in group_keys:
            raise PartOptionsError("part_group_name_duplicate")
        group_keys.add(group_key)
        option_keys: set[str] = set()
        defaults = 0
        options: list[tuple[int, str, str, bool]] = []
        for option in group.options:
            option_name, option_key = _name(option.name)
            if not option_name or option_key in option_keys:
                raise PartOptionsError("part_option_name_duplicate")
            if option.file_id in file_ids:
                raise PartOptionsError("artifact_already_part_option")
            option_keys.add(option_key)
            file_ids.add(option.file_id)
            defaults += int(option.is_default)
            options.append((option.file_id, option_name, option_key, option.is_default))
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
        for option_order, (file_id, option_name, option_key, is_default) in enumerate(
            options
        ):
            session.add(
                PartOption(
                    part_group_id=group.id,
                    file_id=file_id,
                    name=option_name,
                    name_key=option_key,
                    sort_order=option_order,
                    is_default=is_default,
                )
            )
    session.commit()
    return read_for_model(session, model_id)
