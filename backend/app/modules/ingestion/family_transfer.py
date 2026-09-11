"""Portable Family relationships: authorized hashes, stable UUIDs, atomic imports."""

import hashlib
import json
import zipfile
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, select

from app.core.errors import OperationError
from app.db.models import (
    Collection,
    CollectionRole,
    Model,
    ModelFamily,
    ModelFamilyMember,
    ModelFamilyTagLink,
    SavedView,
    Tag,
    User,
)
from app.db.scopes import live
from app.modules.identity import rbac
from app.modules.library import taxonomy
from app.modules.library.families import access, covers, mutations
from app.modules.media.source_cover_processing import (
    MAX_SOURCE_COVER_BYTES,
    process_source_cover_upload,
)
from app.modules.storage.storage_backend.runtime import get_backend
from app.schemas.family_transfer import (
    PortableFamily,
    PortableFamilyCover,
    PortableFamilyMember,
)


@dataclass(frozen=True)
class ExportCover:
    key: str
    entry: str
    size_bytes: int
    sha256: str


def export_saved_views(
    session: Session, user: User, rows: list[SavedView], *, version: int
) -> list[dict]:
    identities = dict(
        session.exec(
            select(ModelFamily.id, ModelFamily.export_id).where(
                live(ModelFamily), access.visible_clause(session, user)
            )
        ).all()
    )
    result = []
    for row in rows:
        filters = json.loads(row.filters_json)
        family_filter = (
            any(key in filters for key in ("family_id", "family_role", "in_family"))
            or filters.get("browse") == "families_collapsed"
        )
        if version == 1 and family_filter:
            continue
        saved = {"name": row.name, "filters": filters}
        family_id = filters.pop("family_id", None)
        if family_id is not None:
            identity = identities.get(family_id)
            if identity is None:
                continue
            saved["family_export_id"] = identity
        result.append(saved)
    return result


def import_saved_views(session: Session, user: User, rows: list[dict]) -> int:
    """Resolve Family filters after import; a missing target must not broaden a view."""
    identities = dict(
        session.exec(
            select(ModelFamily.export_id, ModelFamily.id).where(
                live(ModelFamily), access.visible_clause(session, user)
            )
        ).all()
    )
    conflicts = 0
    for row in rows:
        if (
            session.exec(
                select(SavedView.id).where(
                    SavedView.user_id == user.id, SavedView.name == row["name"]
                )
            ).first()
            is not None
        ):
            continue
        filters = dict(row.get("filters", {}))
        identity = row.get("family_export_id")
        if "family_id" in filters or (
            identity is not None and identity not in identities
        ):
            conflicts += 1
            continue
        if identity is not None:
            filters["family_id"] = identities[identity]
        session.add(
            SavedView(
                user_id=int(user.id), name=row["name"], filters_json=json.dumps(filters)
            )
        )
    session.commit()
    return conflicts


def read_cover(key: str, expected_size: int) -> bytes:
    """Bound remote and local reads before accepting a cover into an archive."""
    if not 0 < expected_size <= MAX_SOURCE_COVER_BYTES:
        raise ValueError("archive_blob_hash_mismatch")
    data = bytearray()
    chunks = get_backend().stream_chunks(key, chunk_size=64 * 1024)
    try:
        for chunk in chunks:
            if len(data) + len(chunk) > expected_size:
                raise ValueError("archive_blob_hash_mismatch")
            data.extend(chunk)
    except OSError as exc:
        raise ValueError("archive_blob_hash_mismatch") from exc
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
    if len(data) != expected_size:
        raise ValueError("archive_blob_hash_mismatch")
    return bytes(data)


def export_families(
    session: Session, user: User, included_models: list[Model]
) -> tuple[list[PortableFamily], list[ExportCover]]:
    families = session.exec(
        select(ModelFamily)
        .where(live(ModelFamily), access.visible_clause(session, user))
        .order_by(ModelFamily.id)
    ).all()
    if not families:
        return [], []
    family_ids = [family.id for family in families]
    included_hashes = {model.id: model.hash for model in included_models}
    memberships: dict[int, list[ModelFamilyMember]] = {}
    for member in session.exec(
        select(ModelFamilyMember)
        .where(
            col(ModelFamilyMember.family_id).in_(family_ids),
            col(ModelFamilyMember.detached_at).is_(None),
        )
        .order_by(ModelFamilyMember.sort_order, ModelFamilyMember.id)
    ).all():
        if member.model_id in included_hashes:
            memberships.setdefault(member.family_id, []).append(member)
    collection_query = select(Collection).where(live(Collection))
    if not user.is_superuser:
        collection_query = collection_query.where(
            col(Collection.id).in_(
                rbac.accessible_collection_ids(session, user, CollectionRole.VIEW)
            )
        )
    paths = {row.id: row.path for row in session.exec(collection_query).all()}
    tags: dict[int, list[str]] = {}
    for family_id, name in session.exec(
        select(ModelFamilyTagLink.family_id, Tag.name)
        .join(Tag, Tag.id == ModelFamilyTagLink.tag_id)
        .where(col(ModelFamilyTagLink.family_id).in_(family_ids))
        .order_by(Tag.name)
    ).all():
        tags.setdefault(family_id, []).append(name)
    result, cover_entries = [], []
    for family in families:
        members = memberships.get(int(family.id), [])
        member_hashes = {
            member.id: included_hashes[member.model_id] for member in members
        }
        portable_cover = None
        key = covers.uploaded_key(family)
        if key is not None:
            data = read_cover(key, family.cover_size_bytes or 0)
            entry = f"family-covers/{family.export_id}.webp"
            digest = hashlib.sha256(data).hexdigest()
            portable_cover = PortableFamilyCover(
                entry=entry, size_bytes=len(data), sha256=digest
            )
            cover_entries.append(ExportCover(key, entry, len(data), digest))
        result.append(
            PortableFamily(
                export_id=family.export_id,
                name=family.name,
                slug=family.slug,
                description=family.description,
                collection=paths.get(family.collection_id),
                tags=tags.get(int(family.id), []),
                canonical_model_hash=member_hashes.get(family.canonical_member_id),
                cover_model_hash=included_hashes.get(family.cover_model_id)
                if any(member.model_id == family.cover_model_id for member in members)
                else None,
                cover_image_url=family.cover_image_url,
                cover=portable_cover,
                members=[
                    PortableFamilyMember(
                        model_hash=included_hashes[member.model_id],
                        role=member.role,
                        transformation_note=member.transformation_note,
                        scale_factor=member.scale_factor,
                        relative_to_model_hash=member_hashes.get(
                            member.relative_to_member_id
                        ),
                        mirrored=member.mirrored,
                        mirror_verified=member.mirror_verified,
                        mirror_reference_model_hash=member_hashes.get(
                            member.mirror_reference_member_id
                        ),
                        relative_review_required=member.relative_review_required
                        or (
                            member.scale_factor is not None
                            and member.relative_to_member_id not in member_hashes
                        )
                        or (
                            member.mirror_verified
                            and member.mirror_reference_member_id not in member_hashes
                        ),
                        sort_order=member.sort_order,
                    )
                    for member in members
                ],
            )
        )
    return result, cover_entries


def cover_bytes(archive: zipfile.ZipFile, cover: PortableFamilyCover) -> bytes:
    try:
        matches = [info for info in archive.infolist() if info.filename == cover.entry]
        if (
            len(matches) != 1
            or matches[0].is_dir()
            or matches[0].file_size != cover.size_bytes
        ):
            raise ValueError("portable_family_cover_invalid")
        with archive.open(matches[0]) as source:
            data = source.read(MAX_SOURCE_COVER_BYTES + 1)
        if (
            len(data) != cover.size_bytes
            or hashlib.sha256(data).hexdigest() != cover.sha256
        ):
            raise ValueError("portable_family_cover_invalid")
        process_source_cover_upload(data, cover.content_type)
        return data
    except (KeyError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise ValueError("portable_family_cover_invalid") from exc


def validate_covers(archive: zipfile.ZipFile, families: list[PortableFamily]) -> None:
    # One bounded image at a time, before the first imported Model is written.
    for family in families:
        if family.cover is not None:
            cover_bytes(archive, family.cover)


def _existing_or_conflicting(
    session: Session, user: User, data: PortableFamily, models: dict[str, Model]
) -> str | None:
    existing = session.exec(
        select(ModelFamily)
        .where(ModelFamily.export_id == data.export_id)
        .with_for_update()
    ).first()
    ids = [
        int(models[member.model_hash].id)
        for member in data.members
        if member.model_hash in models
    ]
    try:
        access.require_models(session, user, ids)
        if existing is not None:
            access.require(
                session, user, int(existing.id), edit=True, include_trashed=True
            )
    except OperationError:
        return "family_conflicts"
    owners = session.exec(
        select(ModelFamilyMember.family_id).where(
            col(ModelFamilyMember.model_id).in_(ids),
            col(ModelFamilyMember.detached_at).is_(None),
        )
    ).all()
    if any(
        owner != (existing.id if existing is not None else None) for owner in owners
    ):
        return "family_conflicts"
    # A reimport never undoes local human edits, detaches, covers, or trash.
    if existing is not None:
        return "skipped_families"
    return None


def import_family(
    session: Session,
    user: User,
    data: PortableFamily,
    models: dict[str, Model],
    archive: zipfile.ZipFile,
) -> dict[str, int]:
    outcome = _existing_or_conflicting(session, user, data, models)
    if outcome is not None:
        session.rollback()
        return {outcome: 1}
    session.commit()  # release reads before independent durable cover reservation
    prepared: covers.PreparedCover | None = None
    committed = False
    try:
        if data.cover is not None:
            prepared = covers.prepare_upload(
                session,
                data.export_id,
                cover_bytes(archive, data.cover),
                data.cover.content_type,
            )
        outcome = _existing_or_conflicting(session, user, data, models)
        if outcome is not None:
            return {outcome: 1}
        collection = taxonomy.resolve_or_create_collection_in_transaction(
            session, data.collection or ""
        )
        if collection is not None:
            rbac.require_collection_role(
                session, user, int(collection.id), CollectionRole.EDIT
            )
        family = ModelFamily(
            export_id=data.export_id,
            name=data.name,
            slug=mutations.available_slug(session, data.slug),
            description=data.description,
            collection_id=collection.id if collection else None,
            cover_image_url=data.cover_image_url,
            created_by=user.id,
            updated_by=user.id,
        )
        session.add(family)
        session.flush()
        members: dict[str, ModelFamilyMember] = {}
        for portable in data.members:
            model = models.get(portable.model_hash)
            if model is None:
                continue
            member = ModelFamilyMember(
                family_id=int(family.id),
                model_id=model.id,
                role="identical" if portable.role == "canonical" else portable.role,
                transformation_note=portable.transformation_note,
                scale_factor=portable.scale_factor,
                mirrored=portable.mirrored,
                mirror_verified=portable.mirror_verified,
                relative_review_required=portable.relative_review_required,
                sort_order=portable.sort_order,
                joined_via="portable",
                created_by=user.id,
                updated_by=user.id,
            )
            session.add(member)
            members[portable.model_hash] = member
        session.flush()
        for portable in data.members:
            member = members.get(portable.model_hash)
            if member is None:
                continue
            scale_base = members.get(portable.relative_to_model_hash or "")
            mirror_base = members.get(portable.mirror_reference_model_hash or "")
            member.relative_to_member_id = scale_base.id if scale_base else None
            member.mirror_reference_member_id = mirror_base.id if mirror_base else None
            member.relative_review_required |= (
                member.scale_factor is not None and scale_base is None
            ) or (member.mirror_verified and mirror_base is None)
            session.add(member)
        canonical = members.get(data.canonical_model_hash or "")
        if canonical is not None:
            family.canonical_member_id = canonical.id
            canonical.role, canonical.scale_factor = "canonical", 1.0
            canonical.relative_to_member_id = canonical.mirror_reference_member_id = (
                canonical.id
            )
            canonical.mirrored, canonical.mirror_verified = False, True
            canonical.relative_review_required = False
            session.add(canonical)
        cover_member = members.get(data.cover_model_hash or "")
        family.cover_model_id = cover_member.model_id if cover_member else None
        if prepared is not None:
            covers.attach_upload(session, family, prepared)
        for tag in taxonomy.resolve_or_create_tags_in_transaction(session, data.tags):
            session.add(
                ModelFamilyTagLink(family_id=int(family.id), tag_id=int(tag.id))
            )
        session.add(family)
        mutations.record(
            session,
            user,
            family,
            "import",
            {"export_id": data.export_id, "member_count": len(members)},
        )
        session.commit()
        committed = True
        result = {"created_families": 1}
        if data.canonical_model_hash is not None and canonical is None:
            result["family_canonical_vacancies"] = 1
        if len(members) != len(data.members):
            result["family_missing_members"] = len(data.members) - len(members)
        return result
    except IntegrityError:
        # Includes a competing import or membership insertion after the read.
        return {"family_conflicts": 1}
    finally:
        if not committed:
            session.rollback()
            if prepared is not None:
                get_backend().rollback_create(prepared.receipt)
