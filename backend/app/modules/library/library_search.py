"""Portable library search and effective-tag selection.

Tags can be attached directly to a Model, inherited from any live ancestor
Collection, or attached to a live Artifact.  This module owns that definition so
the browse query and taxonomy counts cannot drift apart.
"""

from __future__ import annotations

from sqlalchemy import func, or_, union_all
from sqlalchemy.orm import aliased
from sqlmodel import select

from app.db.models import (
    Collection,
    CollectionTagLink,
    File,
    FileTagLink,
    Model,
    ModelProvenanceField,
    ModelProvenanceSource,
    ModelTagLink,
    Tag,
)
from app.db.scopes import live


def _escaped_like(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def effective_tag_pairs(visible_collection_ids=None):
    """Return ``(tag_id, model_id)`` for every live effective tag association."""
    model_collection = aliased(Collection)
    tagged_collection = aliased(Collection)
    descendant = aliased(Collection)

    # Start with tagged collections, then follow indexed parent links. Joining
    # every Model's materialised path against every possible ancestor makes an
    # empty tag list quadratic in the library size on SQLite.
    tagged_descendants = (
        select(
            CollectionTagLink.tag_id.label("tag_id"),
            tagged_collection.id.label("collection_id"),
        )
        .join(
            tagged_collection,
            tagged_collection.id == CollectionTagLink.collection_id,
        )
        .join(Tag, Tag.id == CollectionTagLink.tag_id)
        .where(live(tagged_collection), live(Tag))
        .cte("tagged_collection_descendants", recursive=True)
    )
    tagged_descendants = tagged_descendants.union(
        select(tagged_descendants.c.tag_id, descendant.id).join(
            descendant, descendant.parent_id == tagged_descendants.c.collection_id
        )
    )

    direct = (
        select(
            ModelTagLink.tag_id.label("tag_id"),
            ModelTagLink.model_id.label("model_id"),
        )
        .join(Model, Model.id == ModelTagLink.model_id)
        .join(Tag, Tag.id == ModelTagLink.tag_id)
        .where(live(Model), live(Tag))
    )
    artifact = (
        select(
            FileTagLink.tag_id.label("tag_id"),
            File.model_id.label("model_id"),
        )
        .join(File, File.id == FileTagLink.file_id)
        .join(Model, Model.id == File.model_id)
        .join(Tag, Tag.id == FileTagLink.tag_id)
        .where(live(File), live(Model), live(Tag))
    )
    collection = (
        select(
            tagged_descendants.c.tag_id.label("tag_id"),
            Model.id.label("model_id"),
        )
        .join(
            model_collection, model_collection.id == tagged_descendants.c.collection_id
        )
        .join(Model, Model.collection_id == model_collection.id)
        .where(live(Model), live(model_collection))
    )
    if visible_collection_ids is not None:
        scope = Model.collection_id.in_(visible_collection_ids)  # type: ignore[union-attr]
        direct = direct.where(scope)
        artifact = artifact.where(scope)
        collection = collection.where(scope)
    return union_all(direct, artifact, collection)


def _text_match_model_ids(query: str):
    pattern = _escaped_like(query)
    tag_pairs = effective_tag_pairs().subquery("search_effective_tags")
    model_collection = aliased(Collection)
    ancestor_collection = aliased(Collection)

    artifacts = select(File.model_id).where(
        live(File),
        File.original_filename.ilike(pattern, escape="\\"),
    )
    collections = (
        select(Model.id)
        .select_from(Model)
        .join(model_collection, model_collection.id == Model.collection_id)
        .join(
            ancestor_collection,
            or_(
                model_collection.path == ancestor_collection.path,
                model_collection.path.startswith(ancestor_collection.path + "/"),
            ),
        )
        .where(
            live(model_collection),
            live(ancestor_collection),
            or_(
                ancestor_collection.name.ilike(pattern, escape="\\"),
                ancestor_collection.path.ilike(pattern, escape="\\"),
            ),
        )
    )
    tags = (
        select(tag_pairs.c.model_id)
        .join(Tag, Tag.id == tag_pairs.c.tag_id)
        .where(
            live(Tag),
            or_(
                Tag.name.ilike(pattern, escape="\\"),
                Tag.slug.ilike(pattern, escape="\\"),
            ),
        )
    )
    provenance_sources = select(ModelProvenanceSource.model_id).where(
        or_(
            ModelProvenanceSource.provider.ilike(pattern, escape="\\"),
            ModelProvenanceSource.source_item_id.ilike(pattern, escape="\\"),
            ModelProvenanceSource.canonical_url.ilike(pattern, escape="\\"),
            ModelProvenanceSource.tags_json.ilike(pattern, escape="\\"),
        )
    )
    provenance_fields = (
        select(ModelProvenanceSource.model_id)
        .join(
            ModelProvenanceField,
            ModelProvenanceField.provenance_source_id == ModelProvenanceSource.id,
        )
        .where(
            or_(
                ModelProvenanceField.captured_value_json.ilike(pattern, escape="\\"),
                ModelProvenanceField.user_value_json.ilike(pattern, escape="\\"),
            )
        )
    )
    return union_all(
        artifacts,
        collections,
        tags,
        provenance_sources,
        provenance_fields,
    )


def apply_library_search(
    stmt, *, query: str | None, tag_slugs: list[str], matches=None
):
    """Apply text and effective-tag filters to a Model select."""
    normalized_query = query.strip() if query else ""
    if normalized_query and matches is not None:
        stmt = stmt.where(Model.id.in_(select(matches.c.model_id)))
    elif normalized_query:
        matching_ids = _text_match_model_ids(normalized_query)
        pattern = _escaped_like(normalized_query)
        stmt = stmt.where(
            or_(
                Model.name.ilike(pattern, escape="\\"),
                Model.description.ilike(pattern, escape="\\"),
                Model.source_url.ilike(pattern, escape="\\"),
                Model.id.in_(matching_ids),
            )
        )

    pairs = effective_tag_pairs().subquery("filtered_effective_tags")
    for slug in dict.fromkeys(
        value.strip().lower() for value in tag_slugs if value.strip()
    ):
        matching_ids = (
            select(pairs.c.model_id)
            .join(Tag, Tag.id == pairs.c.tag_id)
            .where(Tag.slug == slug, live(Tag))
        )
        stmt = stmt.where(Model.id.in_(matching_ids))
    return stmt


def accessible_tag_counts_stmt(visible_collection_ids=None):
    """Grouped distinct Model counts with optional collection RBAC scope."""
    pairs = effective_tag_pairs(visible_collection_ids).subquery(
        "counted_effective_tags"
    )
    return select(
        pairs.c.tag_id,
        func.count(func.distinct(pairs.c.model_id)).label("model_count"),
    ).group_by(pairs.c.tag_id)
