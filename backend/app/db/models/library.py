"""Models, Artifacts, taxonomies, multipart compositions and saved library views."""

from datetime import datetime
from typing import List, Optional
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlmodel import Field, Relationship

from app.core.time import utcnow

from .base import SQLModel
from .types import DocumentKind, FileRevisionStatus, FileType


class Metadata(SQLModel, table=True):
    """Slicer-derived facts. 1:1 with File."""

    __tablename__ = "metadata"

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", unique=True, index=True)

    # Slicer
    slicer_name: Optional[str] = Field(default=None, max_length=64)
    slicer_version: Optional[str] = Field(default=None, max_length=32)
    printer_model: Optional[str] = Field(default=None, max_length=128)
    nozzle_diameter_mm: Optional[float] = None
    layer_height_mm: Optional[float] = None
    first_layer_height_mm: Optional[float] = None
    infill_percent: Optional[float] = None
    wall_loops: Optional[int] = None
    top_shell_layers: Optional[int] = None
    bottom_shell_layers: Optional[int] = None
    support_material: Optional[bool] = None
    nozzle_temperature_c: Optional[float] = None
    bed_temperature_c: Optional[float] = None

    # Print stats
    estimated_time_s: Optional[int] = None
    filament_weight_g: Optional[float] = None
    filament_length_mm: Optional[float] = None
    filament_cost: Optional[float] = None
    material_type: Optional[str] = Field(default=None, max_length=64)
    material_brand: Optional[str] = Field(default=None, max_length=128)

    # Geometry (filled later by Trimesh; left None in Stage 1 for STL/3MF)
    bbox_x_mm: Optional[float] = None
    bbox_y_mm: Optional[float] = None
    bbox_z_mm: Optional[float] = None
    volume_mm3: Optional[float] = None
    triangle_count: Optional[int] = None

    created_at: datetime = Field(default_factory=utcnow)

    file: Optional["File"] = Relationship(back_populates="file_metadata")


class ArtifactMaterialRequirement(SQLModel, table=True):
    """Per-tool material facts parsed from one G-code Artifact."""

    __tablename__ = "artifact_material_requirements"
    __table_args__ = (
        UniqueConstraint(
            "file_id", "tool_index", name="uq_artifact_material_requirement_tool"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    file_id: int = Field(foreign_key="files.id", index=True)
    tool_index: int = Field(default=0)
    material_type: Optional[str] = Field(default=None, max_length=64)
    color_hex: Optional[str] = Field(default=None, max_length=16)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class File(SQLModel, table=True):
    """Physical artifact stored on disk; many-to-one with Model."""

    __tablename__ = "files"
    __table_args__ = (
        Index("uq_files_model_version", "model_id", "version", unique=True),
        Index("ix_files_model_deleted_type", "model_id", "deleted_at", "file_type"),
        Index(
            "uq_files_live_recommended_gcode",
            "model_id",
            unique=True,
            sqlite_where=text(
                "file_type = 'GCODE' AND is_recommended = 1 AND deleted_at IS NULL"
            ),
            postgresql_where=text(
                "file_type = 'GCODE' AND is_recommended IS TRUE AND deleted_at IS NULL"
            ),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(foreign_key="models.id", index=True)

    path: str = Field(max_length=1024)
    original_filename: str = Field(max_length=512)
    file_type: FileType = Field(index=True)
    version: int = Field(default=1)
    size_bytes: int
    sha256: str = Field(index=True, max_length=64)
    revision_label: Optional[str] = Field(default=None, max_length=128)
    revision_status: Optional[FileRevisionStatus] = Field(default=None, index=True)
    revision_notes: Optional[str] = None
    is_recommended: bool = Field(default=False, index=True)

    # External libraries (NAS folder mirroring). When ``is_external`` is true the
    # blob lives on a user-managed external root (``path`` is its absolute path on
    # that root) — PrintStash indexes/serves it but never owns or deletes the bytes.
    # ``source_mtime`` is the on-disk mtime captured at scan time, used alongside
    # ``size_bytes`` for cheap change detection on subsequent scans.
    is_external: bool = Field(default=False, index=True)
    external_library_id: Optional[int] = Field(
        default=None, foreign_key="external_libraries.id", index=True
    )
    # Remote libraries keep a provider-relative immutable key here. ``path``
    # remains the stable display/resolution URI and is never interpreted as a
    # local pathname by ArtifactContent.
    source_key: Optional[str] = Field(default=None, max_length=2048, index=True)
    source_mtime: Optional[float] = None
    source_etag: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    source_version_id: Optional[str] = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    # Last time discovery read and hashed the source bytes. Metadata-only scans
    # remain cheap, while a rotating weekly sweep still catches content changes
    # on appliances that preserve both size and mtime.
    source_verified_at: Optional[datetime] = Field(default=None, index=True)
    ingestion_key: Optional[str] = Field(
        default=None, max_length=64, unique=True, index=True
    )
    thumbnail_path: Optional[str] = Field(default=None, max_length=2048)

    uploaded_at: datetime = Field(default_factory=utcnow, index=True)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)

    model: Optional["Model"] = Relationship(
        back_populates="files",
        sa_relationship_kwargs={"foreign_keys": "File.model_id"},
    )
    file_metadata: Optional[Metadata] = Relationship(
        back_populates="file",
        sa_relationship_kwargs={"uselist": False, "cascade": "all, delete-orphan"},
    )


# ---------------------------------------------------------------------------
# Collections (hierarchical) & Tags (flat, many-to-many)
# ---------------------------------------------------------------------------


class Collection(SQLModel, table=True):
    """Hierarchical collection. Self-referential via parent_id.

    `path` is the materialised slash-joined slug chain ("functional/brackets"),
    used for fast filtering and stable URLs.
    """

    __tablename__ = "collections"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=128)
    slug: str = Field(max_length=128, index=True)
    parent_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    path: str = Field(max_length=512, unique=True, index=True)

    # Short markdown description shown on top of the collection view. Image refs
    # point at /collections/{id}/images/{name} (self-hosted).
    readme: Optional[str] = Field(default=None, sa_column=Column(Text))

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class Tag(SQLModel, table=True):
    __tablename__ = "tags"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=64, unique=True, index=True)
    slug: str = Field(max_length=64, unique=True, index=True)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)


class ModelTagLink(SQLModel, table=True):
    """Association table for Model <-> Tag."""

    __tablename__ = "model_tags"

    model_id: Optional[int] = Field(
        default=None, foreign_key="models.id", primary_key=True
    )
    tag_id: Optional[int] = Field(default=None, foreign_key="tags.id", primary_key=True)


class MultipartModelTagLink(SQLModel, table=True):
    """Association table for MultipartModel <-> Tag."""

    __tablename__ = "multipart_model_tags"

    multipart_model_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("multipart_models.id", ondelete="CASCADE"),
            primary_key=True,
        )
    )
    tag_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("tags.id", ondelete="CASCADE"),
            primary_key=True,
            index=True,
        )
    )


class CollectionTagLink(SQLModel, table=True):
    """Association table for Collection <-> Tag."""

    __tablename__ = "collection_tags"

    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", primary_key=True
    )
    tag_id: Optional[int] = Field(
        default=None, foreign_key="tags.id", primary_key=True, index=True
    )


class FileTagLink(SQLModel, table=True):
    """Association table for Artifact <-> Tag."""

    __tablename__ = "file_tags"

    file_id: Optional[int] = Field(
        default=None, foreign_key="files.id", primary_key=True
    )
    tag_id: Optional[int] = Field(
        default=None, foreign_key="tags.id", primary_key=True, index=True
    )


class PartGroup(SQLModel, table=True):
    """One replaceable physical role within a Model."""

    __tablename__ = "part_groups"
    __table_args__ = (
        UniqueConstraint("model_id", "name_key", name="uq_part_groups_model_name_key"),
        UniqueConstraint(
            "model_id", "sort_order", name="uq_part_groups_model_sort_order"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    model_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("models.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    name: str = Field(max_length=128)
    name_key: str = Field(max_length=128)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class PartOption(SQLModel, table=True):
    """One complete printable Model choice within a Part Group.

    ``file_id`` remains nullable for the short-lived artifact-based contract so
    databases upgraded through 0.13.0 keep their data. New writes use
    ``model_id``: the member Model retains its own mesh, preview and revisions.
    """

    __tablename__ = "part_options"
    __table_args__ = (
        UniqueConstraint(
            "part_group_id", "name_key", name="uq_part_options_group_name_key"
        ),
        UniqueConstraint(
            "part_group_id", "sort_order", name="uq_part_options_group_sort_order"
        ),
        UniqueConstraint("file_id", name="uq_part_options_file_id"),
        UniqueConstraint("model_id", name="uq_part_options_model_id"),
        CheckConstraint(
            "(file_id IS NULL) != (model_id IS NULL)",
            name="part_option_exactly_one_target",
        ),
        Index(
            "uq_part_options_one_default",
            "part_group_id",
            unique=True,
            sqlite_where=text("is_default = 1"),
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    part_group_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("part_groups.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    file_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    model_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("models.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    name: str = Field(max_length=128)
    name_key: str = Field(max_length=128)
    sort_order: int = Field(default=0)
    is_default: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)


class ModelStar(SQLModel, table=True):
    """Per-user favorite marker for a live Model."""

    __tablename__ = "model_stars"
    __table_args__ = (
        UniqueConstraint("user_id", "model_id", name="uq_model_stars_user_model"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    model_id: int = Field(foreign_key="models.id", index=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utcnow)


class SavedView(SQLModel, table=True):
    """Named model-list filters owned by one user."""

    __tablename__ = "saved_views"
    __table_args__ = (
        UniqueConstraint("user_id", "name", name="uq_saved_views_user_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    name: str = Field(max_length=128)
    filters_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Model(SQLModel, table=True):
    """Logical asset, deduplicated by `hash` (source mesh sha256, gcode fallback)."""

    __tablename__ = "models"
    __table_args__ = (
        Index("ix_models_deleted_updated_id", "deleted_at", "updated_at", "id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    slug: str = Field(index=True, unique=True, max_length=255)
    hash: str = Field(index=True, unique=True, max_length=64)
    # Allocated atomically before an Artifact is persisted. Keeping the counter
    # on the owning Model turns version selection into a row-level write instead
    # of the racy ``MAX(files.version) + 1`` read.
    next_file_version: int = Field(
        default=1,
        sa_column=Column(Integer, nullable=False, server_default="1"),
    )

    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    description: Optional[str] = None
    source_url: Optional[str] = Field(default=None, max_length=2048)
    thumbnail_path: Optional[str] = Field(default=None, max_length=512)
    thumbnail_file_id: Optional[int] = Field(default=None, foreign_key="files.id")

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)

    files: List[File] = Relationship(
        back_populates="model",
        sa_relationship_kwargs={
            "cascade": "all, delete-orphan",
            "foreign_keys": "File.model_id",
        },
    )
    tags: List[Tag] = Relationship(
        link_model=ModelTagLink,
        sa_relationship_kwargs={"lazy": "selectin"},
    )
    collection_rel: Optional["Collection"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "Model.collection_id == Collection.id",
            "lazy": "selectin",
        },
    )


class ModelFamily(SQLModel, table=True):
    """Human grouping of variations; never an owner of Model content."""

    __tablename__ = "model_families"
    __table_args__ = (
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_model_families_deleted_updated_id", "deleted_at", "updated_at", "id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, index=True)
    slug: str = Field(max_length=255, unique=True, index=True)
    export_id: str = Field(
        default_factory=lambda: str(uuid4()), max_length=36, unique=True, index=True
    )
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    cover_model_id: Optional[int] = Field(
        default=None, foreign_key="models.id", ondelete="SET NULL", index=True
    )
    cover_image_url: Optional[str] = Field(default=None, max_length=2083)
    cover_filename: Optional[str] = Field(default=None, max_length=255)
    cover_content_type: Optional[str] = Field(default=None, max_length=64)
    cover_size_bytes: Optional[int] = Field(default=None, ge=0)
    canonical_member_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey(
                "model_family_members.id",
                name="fk_model_families_canonical_member_id_model_family_members",
                ondelete="SET NULL",
                use_alter=True,
            ),
            nullable=True,
        ),
    )
    version: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, server_default="1")
    )
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")


class ModelFamilyMember(SQLModel, table=True):
    """Reserved membership or detached history, with independent human intent."""

    __tablename__ = "model_family_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('canonical', 'identical', 'rescaled', 'mirrored', "
            "'repaired', 'print_variant')",
            name="role_valid",
        ),
        CheckConstraint(
            "scale_factor IS NULL OR "
            "(scale_factor > 0 AND scale_factor <= 1.7976931348623157e308)",
            name="scale_positive_finite",
        ),
        CheckConstraint(
            "model_id IS NOT NULL OR "
            "(detached_at IS NOT NULL AND detach_reason = 'model_purged')",
            name="missing_model_is_purged",
        ),
        CheckConstraint(
            "(detached_at IS NULL AND detach_reason IS NULL) OR "
            "(detached_at IS NOT NULL AND detach_reason IS NOT NULL AND detach_reason IN "
            "('removed', 'moved', 'family_trashed', 'model_purged'))",
            name="detach_state_valid",
        ),
        Index(
            "uq_model_family_members_active_model",
            "model_id",
            unique=True,
            sqlite_where=text("detached_at IS NULL"),
            postgresql_where=text("detached_at IS NULL"),
        ),
        Index(
            "uq_model_family_members_active_canonical",
            "family_id",
            unique=True,
            sqlite_where=text("detached_at IS NULL AND role = 'canonical'"),
            postgresql_where=text("detached_at IS NULL AND role = 'canonical'"),
        ),
        Index(
            "ix_model_family_members_family_detached_order",
            "family_id",
            "detached_at",
            "sort_order",
            "id",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    family_id: int = Field(foreign_key="model_families.id", ondelete="CASCADE")
    model_id: Optional[int] = Field(default=None, foreign_key="models.id", index=True)
    role: str = Field(default="identical", max_length=32)
    transformation_note: Optional[str] = Field(default=None, sa_column=Column(Text))
    scale_factor: Optional[float] = None
    mirrored: bool = False
    relative_to_member_id: Optional[int] = Field(
        default=None, foreign_key="model_family_members.id", ondelete="SET NULL"
    )
    mirror_reference_member_id: Optional[int] = Field(
        default=None, foreign_key="model_family_members.id", ondelete="SET NULL"
    )
    mirror_verified: bool = False
    relative_review_required: bool = False
    joined_via: str = Field(default="manual", max_length=32)
    sort_order: int = Field(default=0)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    detached_at: Optional[datetime] = None
    detached_by: Optional[int] = Field(default=None, foreign_key="users.id")
    detach_reason: Optional[str] = Field(default=None, max_length=32)


class ModelFamilyTagLink(SQLModel, table=True):
    """Family tags, independent of every member's tags."""

    __tablename__ = "model_family_tags"
    family_id: int = Field(
        foreign_key="model_families.id", primary_key=True, ondelete="CASCADE"
    )
    tag_id: int = Field(foreign_key="tags.id", primary_key=True, ondelete="CASCADE")


class ModelFamilyStar(SQLModel, table=True):
    """A personal Family favorite, independent of Model favorites."""

    __tablename__ = "model_family_stars"
    family_id: int = Field(
        foreign_key="model_families.id", primary_key=True, ondelete="CASCADE"
    )
    user_id: int = Field(foreign_key="users.id", primary_key=True, ondelete="CASCADE")
    created_at: datetime = Field(default_factory=utcnow)


class MultipartModel(SQLModel, table=True):
    """A named composition of printable Models.

    This is deliberately separate from ``Model``: composing models never moves
    or owns their files and revisions. The organised library may group member
    cards under this entity, while their identity and direct routes remain
    intact. A model may be used by many multipart models.
    """

    __tablename__ = "multipart_models"
    __table_args__ = (UniqueConstraint("slug", name="uq_multipart_models_slug"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=255, index=True)
    slug: str = Field(max_length=255, index=True)
    description: Optional[str] = Field(default=None, sa_column=Column(Text))
    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    cover_model_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("models.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    cover_image_url: Optional[str] = Field(default=None, max_length=2083)
    cover_filename: Optional[str] = Field(default=None, max_length=255)
    cover_content_type: Optional[str] = Field(default=None, max_length=64)
    cover_size_bytes: Optional[int] = Field(default=None, ge=0)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class MultipartModelStar(SQLModel, table=True):
    """Per-user favorite marker for a multipart set."""

    __tablename__ = "multipart_model_stars"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "multipart_model_id",
            name="uq_multipart_model_stars_user_model",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True, ondelete="CASCADE")
    multipart_model_id: int = Field(
        foreign_key="multipart_models.id", index=True, ondelete="CASCADE"
    )
    created_at: datetime = Field(default_factory=utcnow)


class MultipartPart(SQLModel, table=True):
    """A named physical part in a ``MultipartModel``."""

    __tablename__ = "multipart_parts"
    __table_args__ = (
        UniqueConstraint(
            "multipart_model_id", "name_key", name="uq_multipart_parts_model_name_key"
        ),
        UniqueConstraint(
            "multipart_model_id",
            "sort_order",
            name="uq_multipart_parts_model_sort_order",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    multipart_model_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("multipart_models.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    quantity: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, server_default="1")
    )
    name: str = Field(max_length=128)
    name_key: str = Field(max_length=128)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MultipartModelChoice(SQLModel, table=True):
    """A Model selected as one possible choice for a multipart part.

    ``source_file_id`` and ``label`` retain the identity of choices imported
    from the pre-0.13 nested part-option tables.  A legacy composition may
    contain more than one file from the same Model, so the source file is
    deliberately metadata rather than another uniqueness key.
    """

    __tablename__ = "multipart_model_choices"
    __table_args__ = (
        UniqueConstraint(
            "multipart_part_id",
            "sort_order",
            name="uq_multipart_choices_part_sort_order",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    multipart_model_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("multipart_models.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    multipart_part_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("multipart_parts.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    # Do not cascade Model deletion into the aggregate.  Purge detaches this
    # row explicitly and removes an empty part; normal DB deletes are guarded by
    # the FK so a caller cannot silently lose composition data.
    model_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("models.id"),
            nullable=False,
            index=True,
        )
    )
    source_file_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )
    label: Optional[str] = Field(default=None, max_length=128)
    sort_order: int = Field(default=0)
    created_at: datetime = Field(default_factory=utcnow)


class Document(SQLModel, table=True):
    """A standalone document item (manual / notes) living in a collection,
    shown in the library alongside models. Markdown docs keep their content in
    ``body`` (editable); binary docs (PDF/other) store ``filename`` + a blob."""

    __tablename__ = "documents"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=255)
    kind: DocumentKind = Field(index=True)
    collection_id: Optional[int] = Field(
        default=None, foreign_key="collections.id", index=True
    )
    multipart_model_id: Optional[int] = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("multipart_models.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
    )

    body: Optional[str] = Field(default=None, sa_column=Column(Text))  # markdown
    filename: Optional[str] = Field(default=None, max_length=255)  # binary
    size_bytes: Optional[int] = None
    sha256: Optional[str] = Field(default=None, max_length=64)

    deleted_at: Optional[datetime] = Field(default=None, index=True)
    deleted_by: Optional[int] = Field(default=None, foreign_key="users.id")
    purge_token: Optional[str] = Field(default=None, max_length=64, index=True)
    created_by: Optional[int] = Field(default=None, foreign_key="users.id")
    updated_by: Optional[int] = Field(default=None, foreign_key="users.id")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
