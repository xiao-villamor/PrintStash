"""Builders for the library itself: models, artifacts, metadata, collections, tags.

These are the rows almost every test needs, and the ones where the raw table is
most misleading. Three traps the builders take away:

* **A trashed row is `deleted_at`, not a flag.** Every read path filters through
  `scopes.live()`, so `trashed=True` here is the difference between testing the
  live path and testing nothing. Spelling it as a keyword means a test never has
  to import `utcnow` to hide a row.
* **`next_file_version` is the model's own counter.** An artifact's `version`
  comes from the model it hangs under, and two artifacts sharing a version is a
  state the app cannot produce. `build_file` advances the counter the way
  ingestion does.
* **`is_recommended` is an invariant, not a column.** At most one live G-code
  revision per model is the recommended one. `build_file(recommended=True)`
  clears the previous holder, so a test setting up "three revisions, the newest
  recommended" gets the state the app would actually be in.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    Collection,
    File,
    FileRevisionStatus,
    FileType,
    Metadata,
    Model,
    ModelTagLink,
    Tag,
)
from tests.factories._support import nth, reject_aliases, save, unique_hash


def detached_model(**overrides: Any) -> Model:
    """A `Model` that is deliberately never attached to a session.

    Two things need one. A purge claim must refuse a row that was never
    persisted, and the stale-write tests need a *second* object carrying an
    existing row's primary key so the claim sees a version it did not read.
    Both are states the application can reach and neither may be saved, so
    `build_model` is the wrong tool and the shape still belongs here.

    See `detached_file` and `detached_collection` for the same idea on the other
    two tables: the pattern is "the row's *absence* from the database is what the
    test asserts on", which is why these exist beside the builders rather than
    being spelled inline.
    """
    overrides.setdefault("name", "Detached")
    overrides.setdefault("slug", "detached")
    overrides.setdefault("hash", "0" * 64)
    return Model(**overrides)


def detached_file(**overrides: Any) -> File:
    """A `File` that is deliberately never attached to a session.

    Several guards exist precisely to refuse an artifact with no id — a hard
    delete cannot reason about bytes it has no row for, and the vault audit must
    not credit ownership to something that was never written. A saved row cannot
    reach those branches at all.
    """
    overrides.setdefault("model_id", 1)
    overrides.setdefault("original_filename", "detached.stl")
    overrides.setdefault("path", "detached/detached.stl")
    overrides.setdefault("file_type", FileType.STL)
    overrides.setdefault("version", 1)
    overrides.setdefault("size_bytes", 1)
    overrides.setdefault("sha256", "0" * 64)
    return File(**overrides)


def detached_collection(**overrides: Any) -> Collection:
    """A `Collection` that is deliberately never attached to a session."""
    overrides.setdefault("name", "Detached")
    overrides.setdefault("slug", "detached")
    overrides.setdefault("path", "detached")
    return Collection(**overrides)


def build_model(
    session: Session,
    name: str = "Bracket",
    *,
    collection: Collection | None = None,
    trashed: bool | datetime = False,
    **overrides: Any,
) -> Model:
    """A library model.

    `trashed=True` puts it in the trash now; pass a datetime to control when, for
    a retention-window test. `collection` is the row, not its id, so a test never
    has to reach for `.id` on something it just built.
    """
    reject_aliases(overrides, {"deleted_at": "trashed"} if trashed else {})
    index = nth("model")
    if collection is not None:
        overrides.setdefault("collection_id", collection.id)
    if trashed:
        overrides.setdefault(
            "deleted_at", trashed if isinstance(trashed, datetime) else utcnow()
        )
    overrides.setdefault("slug", f"model-{index}")
    overrides.setdefault("hash", f"{index:064d}")
    return save(session, Model(name=name, **overrides))


def build_file(
    session: Session,
    model: Model,
    *,
    file_type: FileType | None = None,
    filename: str | None = None,
    recommended: bool = False,
    status: FileRevisionStatus | None = None,
    trashed: bool | datetime = False,
    external: bool = False,
    metadata: dict[str, Any] | None = None,
    **overrides: Any,
) -> File:
    """One artifact under *model*, at the model's next version.

    `recommended=True` also demotes whatever revision held the recommendation, so
    the "exactly one recommended live G-code" invariant holds without the test
    having to know about it.

    `metadata={"material_type": "PLA"}` attaches the slicer metadata row in the
    same call. Twenty test files need the pair, and the reason is structural: the
    structured filters and the cost calculations read the metadata of *this*
    artifact, so a file whose metadata hangs off a sibling matches nothing — a
    result that looks like a filter bug and is a setup bug.
    """
    reject_aliases(
        overrides,
        {
            "is_recommended": "recommended",
            "is_external": "external",
            "revision_status": "status",
            "original_filename": "filename",
        },
    )
    file_type = file_type or _type_from(filename)
    index = nth("file")
    version = overrides.pop("version", None)
    if version is None:
        version = model.next_file_version
        model.next_file_version += 1
        session.add(model)
    name = filename or f"artifact-{index}.{file_type.value.lower()}"
    if trashed:
        overrides.setdefault(
            "deleted_at", trashed if isinstance(trashed, datetime) else utcnow()
        )
    if recommended:
        _demote_current_recommendation(session, model)
    overrides.setdefault("path", f"{model.slug}/v{version}/{name}")
    overrides.setdefault("size_bytes", 1)
    overrides.setdefault("sha256", unique_hash("file_sha"))
    row = save(
        session,
        File(
            model_id=model.id,
            original_filename=name,
            file_type=file_type,
            version=version,
            revision_status=status,
            is_recommended=recommended,
            is_external=external,
            **overrides,
        ),
    )
    if metadata is not None:
        build_metadata(session, row, **metadata)
    return row


def _type_from(filename: str | None) -> FileType:
    """Derive the artifact type from its extension, defaulting to G-code.

    Named types and named filenames have to agree, and when they disagree the
    result is quietly wrong rather than an error: a `File` row of type `GCODE`
    called `part.stl` is skipped by every mesh path and picked up by every
    G-code path, so a test builds what it thinks is a mesh and asserts against
    a list that never contains it. That happened here. Deriving the type removes
    the chance to disagree; pass `file_type=` explicitly when the mismatch is
    the thing under test.
    """
    if filename is None:
        return FileType.GCODE
    suffix = filename.rsplit(".", 1)[-1].lower()
    try:
        return FileType(suffix)
    except ValueError:
        return FileType.GCODE


def _demote_current_recommendation(session: Session, model: Model) -> None:
    for row in session.exec(
        select(File).where(File.model_id == model.id, File.is_recommended == True)  # noqa: E712
    ).all():
        row.is_recommended = False
        session.add(row)


def build_metadata(session: Session, file: File, **overrides: Any) -> Metadata:
    """Slicer/mesh metadata for one artifact.

    Every field is optional in production: a mesh has no slicer settings, and a
    G-code file from an unrecognised slicer parses to all-`None`. So this builder
    defaults to empty and the test names only what it asserts on.
    """
    return save(session, Metadata(file_id=file.id, **overrides))


def build_collection(
    session: Session,
    name: str = "Parts",
    *,
    parent: Collection | None = None,
    **overrides: Any,
) -> Collection:
    """A collection.

    `path` is the materialized ancestry the API returns and RBAC reads, so
    passing `parent` keeps it consistent instead of leaving a child whose path
    claims it is at the root.
    """
    slug = overrides.pop("slug", None) or f"{name.lower().replace(' ', '-')}"
    if parent is not None:
        overrides.setdefault("parent_id", parent.id)
        overrides.setdefault("path", f"{parent.path}/{slug}")
    else:
        overrides.setdefault("path", slug)
    return save(session, Collection(name=name, slug=slug, **overrides))


def build_tag(session: Session, name: str = "functional", **overrides: Any) -> Tag:
    overrides.setdefault("slug", name.lower().replace(" ", "-"))
    return save(session, Tag(name=name, **overrides))


def tag_model(session: Session, model: Model, tag: Tag) -> None:
    """Attach an existing tag to a model, the way the taxonomy service would."""
    session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
    session.commit()
