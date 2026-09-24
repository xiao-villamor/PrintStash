"""Store extensible FileType names as text, including DXF.

Revision ID: 6f27f2e6090a
Revises: a23d2e57ff0c
Create Date: 2026-09-24 01:15:47.755495

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision = "6f27f2e6090a"
down_revision = "a23d2e57ff0c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Alembic generated these three operations from the PostgreSQL old schema.
    # Its default order tries to convert a column while the old partial index
    # still compares it to a native filetype literal, so drop that index first.
    with op.batch_alter_table("files") as batch_op:
        batch_op.drop_index("uq_files_live_recommended_gcode")
    if op.get_bind().dialect.name == "postgresql":
        op.alter_column(
            "files",
            "file_type",
            existing_type=postgresql.ENUM(
                "STL", "THREE_MF", "GCODE", "OBJ", "STEP", name="filetype"
            ),
            type_=sa.Enum(
                "STL",
                "THREE_MF",
                "GCODE",
                "OBJ",
                "STEP",
                "DXF",
                name="filetype",
                native_enum=False,
            ),
            existing_nullable=False,
            postgresql_using="file_type::text",
        )
    with op.batch_alter_table("files") as batch_op:
        batch_op.create_index(
            "uq_files_live_recommended_gcode_text",
            ["model_id"],
            unique=True,
            sqlite_where=sa.text(
                "file_type = 'GCODE' AND is_recommended = 1 AND deleted_at IS NULL"
            ),
            postgresql_where=sa.text(
                "file_type = 'GCODE' AND is_recommended IS TRUE AND deleted_at IS NULL"
            ),
        )


def downgrade() -> None:
    # Old application versions cannot decode DXF. Refuse to roll back rather
    # than hiding or dropping user-owned originals.
    if (
        op.get_bind()
        .execute(sa.text("SELECT 1 FROM files WHERE file_type = 'DXF' LIMIT 1"))
        .first()
    ):
        raise RuntimeError("remove or export DXF Artifacts before downgrading")
    with op.batch_alter_table("files") as batch_op:
        batch_op.drop_index("uq_files_live_recommended_gcode_text")
    if op.get_bind().dialect.name == "postgresql":
        old_type = postgresql.ENUM(
            "STL", "THREE_MF", "GCODE", "OBJ", "STEP", name="filetype"
        )
        # A database created from current metadata and stamped at head has no
        # native enum. Recreate it for downgrade; on an upgraded database the
        # pre-existing type is retained.
        old_type.create(op.get_bind(), checkfirst=True)
        op.alter_column(
            "files",
            "file_type",
            existing_type=sa.Enum(
                "STL",
                "THREE_MF",
                "GCODE",
                "OBJ",
                "STEP",
                "DXF",
                name="filetype",
                native_enum=False,
            ),
            type_=old_type,
            existing_nullable=False,
            postgresql_using="file_type::filetype",
        )
    with op.batch_alter_table("files") as batch_op:
        batch_op.create_index(
            "uq_files_live_recommended_gcode",
            ["model_id"],
            unique=True,
            sqlite_where=sa.text(
                "file_type = 'GCODE' AND is_recommended = 1 AND deleted_at IS NULL"
            ),
            postgresql_where=sa.text(
                "file_type = 'GCODE' AND is_recommended IS TRUE AND deleted_at IS NULL"
            ),
        )
