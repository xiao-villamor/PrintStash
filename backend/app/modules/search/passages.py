"""Transactional passage replacement without inference or an implicit commit.

The injected content projection port and reconciliation worker both use the
same replacement operation. Derived lexical/vector consumers retain separate
contracts and never cause inference in the source transaction.
"""

from __future__ import annotations

import json
import secrets
import unicodedata
from dataclasses import dataclass, replace

from printstash_core.search.passages import (
    RECIPE_VERSION,
    SearchSubject,
    access_identity,
    render_passages,
)
from printstash_core.search.text_inputs import TextRecipe
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import EmbeddingSpace, IndexGeneration, SubjectCaption
from app.db.models.search import SearchDependency, SearchPassage
from app.db.projections import ContentSource
from app.modules.search import lexical_index
from app.modules.search.configuration import settings
from app.modules.search.dependencies import extraction_dependencies
from app.modules.search.invalidation import invalidate_passage
from app.modules.search.sources import project_subject


@dataclass(frozen=True)
class PassageChanges:
    inserted: int = 0
    updated: int = 0
    removed: int = 0


def sync_subject(session: Session, subject: SearchSubject) -> PassageChanges:
    """Replace this recipe only; missing/trashed Subjects lose every recipe.

    The caller serializes writes to a Subject and owns commit/rollback. Stable
    passage IDs and hashes let later backfill workers distinguish an indexed
    content edit from unrelated bookkeeping without relying on timestamps.
    """
    projection = project_subject(session, subject)
    dependencies = extraction_dependencies(session, subject) if projection else set()
    previous = session.exec(
        select(SearchDependency).where(
            SearchDependency.subject_type == subject.subject_type.value,
            SearchDependency.subject_id == subject.subject_id,
        )
    ).all()
    for dependency in previous:
        key = ContentSource(dependency.source_kind, dependency.source_id)
        if key in dependencies:
            dependencies.remove(key)
        else:
            session.delete(dependency)
    for dependency in dependencies:
        session.add(
            SearchDependency(
                subject_type=subject.subject_type.value,
                subject_id=subject.subject_id,
                source_kind=dependency.kind,
                source_id=dependency.id,
            )
        )
    statement = select(SearchPassage).where(
        SearchPassage.subject_type == subject.subject_type.value,
        SearchPassage.subject_id == subject.subject_id,
    )
    rows = session.exec(statement).all()
    if projection is None:
        for row in rows:
            invalidate_passage(session, row.id)
            lexical_index.replace(
                session, row, lexical_index.snapshot(row), deleted=True
            )
            session.delete(row)
        session.flush()
        return PassageChanges(removed=len(rows))
    caption = session.exec(
        select(SubjectCaption).where(
            SubjectCaption.subject_type == subject.subject_type.value,
            SubjectCaption.subject_id == subject.subject_id,
        )
    ).first()
    caption_text = caption.text if caption and caption.state != "dismissed" else ""
    if caption and caption.state == "generated" and caption.text:
        from app.modules.search import caption_source

        file = caption_source.source(session, subject)
        if (
            file is None
            or file.id != caption.source_file_id
            or file.sha256 != caption.input_hash
            or caption.recipe != caption_source.RECIPE
        ):
            # Source edits remove obsolete generated claims in the same content
            # transaction, before the bounded sweep schedules replacement work.
            caption.text = ""
            caption_text = ""
            caption.phase = "failed"
            caption.updated_at = utcnow()
            caption.lease_token, caption.lease_expires_at = None, None
            caption.error_code = "caption_source_changed"
            caption.version_token = secrets.token_hex(16)
            session.add(caption)
            session.flush()
    versions = {RECIPE_VERSION}
    if (
        caption
        or settings(session).captions_enabled
        or any(row.recipe_version == 2 for row in rows)
    ):
        versions.add(2)
    # A blue/green v2 build needs its passages before inference begins.
    recipes = session.exec(
        select(EmbeddingSpace.recipe_json)
        .join(IndexGeneration, IndexGeneration.space_id == EmbeddingSpace.id)
        .where(
            EmbeddingSpace.profile == "semantic_text",
            IndexGeneration.state.in_(("building", "ready", "active")),
        )
    ).all()
    for recipe in recipes:
        try:
            versions.add(TextRecipe(**json.loads(recipe)).passage_version)
        except (TypeError, ValueError):
            continue
    existing = {
        (row.recipe_version, row.visibility_segment_key, row.chunk_index): row
        for row in rows
        if row.recipe_version in versions
    }
    inserted = updated = 0
    for version in sorted(versions):
        for segment in projection.segments:
            segment_key, dependencies_json = access_identity(
                segment.access_dependencies
            )
            content = replace(
                segment.content,
                caption=caption_text if not segment.access_dependencies else "",
            )
            for passage in render_passages(content, recipe_version=version):
                key = (version, segment_key, passage.chunk_index)
                row = existing.pop(key, None)
                old_lexical = lexical_index.snapshot(row) if row is not None else None
                values = {
                    "access_dependencies_json": dependencies_json,
                    "content_hash": passage.content_hash,
                    "text": passage.text,
                    "truncated": passage.truncated or segment.truncated,
                    "title": unicodedata.normalize(
                        "NFC", " ".join(content.title.split())
                    )[:4096],
                    "tags_text": unicodedata.normalize(
                        "NFC", " ".join(content.tags[:64])
                    )[:8192],
                }
                if row is None:
                    row = SearchPassage(
                        subject_type=subject.subject_type.value,
                        subject_id=subject.subject_id,
                        visibility_segment_key=segment_key,
                        chunk_index=passage.chunk_index,
                        recipe_version=version,
                        source_updated_at=projection.source_updated_at,
                        **values,
                    )
                    inserted += 1
                elif any(
                    getattr(row, field) != value for field, value in values.items()
                ):
                    invalidate_passage(session, row.id)
                    for field, value in values.items():
                        setattr(row, field, value)
                    row.updated_at = utcnow()
                    updated += 1
                row.source_updated_at = projection.source_updated_at
                session.add(row)
                session.flush()
                lexical_index.replace(
                    session, row, old_lexical, deleted=version != max(versions)
                )
    for row in existing.values():
        invalidate_passage(session, row.id)
        lexical_index.replace(session, row, lexical_index.snapshot(row), deleted=True)
        session.delete(row)
    session.flush()
    return PassageChanges(inserted=inserted, updated=updated, removed=len(existing))
