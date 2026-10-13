"""retain closed mesh volume measurement evidence

Revision ID: 8fd749bf52a1
Revises: 67494831ae72
Create Date: 2026-10-04 19:12:32.645873

"""

import logging

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8fd749bf52a1"
down_revision: Union[str, Sequence[str], None] = "67494831ae72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _metadata_table(*, constrained: bool) -> sa.Table:
    """Freeze this revision's table, preserving its keys and unique file index."""
    metadata = sa.MetaData()
    sa.Table("files", metadata, sa.Column("id", sa.Integer(), primary_key=True))
    columns = [
        sa.Column("id", sa.INTEGER(), nullable=False),
        sa.Column("file_id", sa.INTEGER(), nullable=False),
        sa.Column("slicer_name", sa.VARCHAR(length=64), nullable=True),
        sa.Column("slicer_version", sa.VARCHAR(length=32), nullable=True),
        sa.Column("printer_model", sa.VARCHAR(length=128), nullable=True),
        sa.Column("nozzle_diameter_mm", sa.FLOAT(), nullable=True),
        sa.Column("layer_height_mm", sa.FLOAT(), nullable=True),
        sa.Column("infill_percent", sa.FLOAT(), nullable=True),
        sa.Column("estimated_time_s", sa.INTEGER(), nullable=True),
        sa.Column("filament_weight_g", sa.FLOAT(), nullable=True),
        sa.Column("filament_length_mm", sa.FLOAT(), nullable=True),
        sa.Column("filament_cost", sa.FLOAT(), nullable=True),
        sa.Column("material_type", sa.VARCHAR(length=64), nullable=True),
        sa.Column("material_brand", sa.VARCHAR(length=128), nullable=True),
        sa.Column("bbox_x_mm", sa.FLOAT(), nullable=True),
        sa.Column("bbox_y_mm", sa.FLOAT(), nullable=True),
        sa.Column("bbox_z_mm", sa.FLOAT(), nullable=True),
        sa.Column("volume_mm3", sa.FLOAT(), nullable=True),
        sa.Column("triangle_count", sa.INTEGER(), nullable=True),
        sa.Column("created_at", sa.DATETIME(), nullable=False),
        sa.Column("first_layer_height_mm", sa.FLOAT(), nullable=True),
        sa.Column("wall_loops", sa.INTEGER(), nullable=True),
        sa.Column("top_shell_layers", sa.INTEGER(), nullable=True),
        sa.Column("bottom_shell_layers", sa.INTEGER(), nullable=True),
        sa.Column("support_material", sa.BOOLEAN(), nullable=True),
        sa.Column("nozzle_temperature_c", sa.FLOAT(), nullable=True),
        sa.Column("bed_temperature_c", sa.FLOAT(), nullable=True),
        sa.Column("native_context_json", sa.TEXT(), nullable=True),
        sa.Column(
            "volume_state",
            sa.Text(),
            nullable=False,
            server_default="not_calculated" if constrained else "legacy_unassessed",
        ),
        sa.Column("volume_method", sa.Text(), nullable=True),
        sa.Column("volume_unavailable_cause", sa.Text(), nullable=True),
        sa.Column(
            "volume_not_calculated_cause",
            sa.Text(),
            nullable=True,
            server_default="enrichment_pending" if constrained else None,
        ),
    ]
    constraints: list[sa.Constraint] = [
        sa.PrimaryKeyConstraint("id", name="pk_metadata"),
        sa.ForeignKeyConstraint(
            ["file_id"], ["files.id"], name="fk_metadata_file_id_files"
        ),
    ]
    if constrained:
        constraints.extend(
            [
                sa.CheckConstraint(
                    "bbox_x_mm IS NULL OR (bbox_x_mm >= 0 AND bbox_x_mm <= 1.7976931348623157e+308)",
                    name="ck_metadata_bbox_x_mm_physical",
                ),
                sa.CheckConstraint(
                    "bbox_y_mm IS NULL OR (bbox_y_mm >= 0 AND bbox_y_mm <= 1.7976931348623157e+308)",
                    name="ck_metadata_bbox_y_mm_physical",
                ),
                sa.CheckConstraint(
                    "bbox_z_mm IS NULL OR (bbox_z_mm >= 0 AND bbox_z_mm <= 1.7976931348623157e+308)",
                    name="ck_metadata_bbox_z_mm_physical",
                ),
                sa.CheckConstraint(
                    "(volume_state = 'measured' AND volume_mm3 IS NOT NULL AND volume_mm3 > 0 AND volume_mm3 <= 1.7976931348623157e+308 AND volume_method IS NOT NULL AND volume_method = 'mesh_surface_integral' AND volume_unavailable_cause IS NULL AND volume_not_calculated_cause IS NULL) OR (volume_state = 'unavailable' AND volume_mm3 IS NULL AND volume_method IS NOT NULL AND volume_method = 'mesh_surface_integral' AND volume_unavailable_cause IS NOT NULL AND volume_not_calculated_cause IS NULL) OR (volume_state = 'not_calculated' AND volume_mm3 IS NULL AND volume_method IS NULL AND volume_unavailable_cause IS NULL AND volume_not_calculated_cause IS NOT NULL) OR (volume_state = 'legacy_unassessed' AND (volume_mm3 IS NULL OR (volume_mm3 >= -1.7976931348623157e+308 AND volume_mm3 <= 1.7976931348623157e+308)) AND volume_method IS NULL AND volume_unavailable_cause IS NULL AND volume_not_calculated_cause IS NULL)",
                    name="ck_metadata_volume_evidence",
                ),
                sa.CheckConstraint(
                    "volume_method IN ('mesh_surface_integral')",
                    name="ck_metadata_volume_method_values",
                ),
                sa.CheckConstraint(
                    "volume_not_calculated_cause IN ('enrichment_pending', 'not_applicable', 'not_requested', 'geometry_unavailable', 'topology_not_evaluated')",
                    name="ck_metadata_volume_not_calculated_cause_values",
                ),
                sa.CheckConstraint(
                    "volume_state IN ('measured', 'unavailable', 'not_calculated', 'legacy_unassessed')",
                    name="ck_metadata_volume_state_values",
                ),
                sa.CheckConstraint(
                    "volume_unavailable_cause IN ('not_watertight', 'inconsistent_winding', 'non_positive_integral', 'nonfinite_integral', 'measurement_failed')",
                    name="ck_metadata_volume_unavailable_cause_values",
                ),
            ]
        )
    table = sa.Table("metadata", metadata, *columns, *constraints)
    sa.Index("ix_metadata_file_id", table.c.file_id, unique=True)
    return table


def upgrade() -> None:
    """Retain historical scalars as unassessed; new rows start pending."""
    # ### commands auto generated by Alembic - please adjust! ###
    # Native ADD COLUMN preserves old values without rebuilding SQLite's table.
    # These temporary defaults describe pre-evidence rows, never certify them.
    op.add_column(
        "metadata",
        sa.Column(
            "volume_state",
            sa.Text(),
            server_default="legacy_unassessed",
            nullable=False,
        ),
    )
    op.add_column("metadata", sa.Column("volume_method", sa.Text(), nullable=True))
    op.add_column(
        "metadata", sa.Column("volume_unavailable_cause", sa.Text(), nullable=True)
    )
    op.add_column(
        "metadata", sa.Column("volume_not_calculated_cause", sa.Text(), nullable=True)
    )
    # Old scalar-only callers could persist infinity (or PostgreSQL NaN).
    # Those facts cannot be represented by the finite public contract. Replace
    # only these unusable scalars with unknown, retaining unassessed provenance
    # and every other fact. Downgrade intentionally cannot recover them.
    table = _metadata_table(constrained=False)
    repair_statement = (
        table.update()
        .where(
            sa.or_(
                table.c.volume_mm3 > 1.7976931348623157e308,
                table.c.volume_mm3 < -1.7976931348623157e308,
            )
        )
        .values(volume_mm3=None)
    )
    if op.get_context().as_sql:
        op.execute(repair_statement)
    else:
        repaired = op.get_bind().execute(repair_statement).rowcount
        if repaired > 0:
            logging.getLogger("alembic.runtime.migration").warning(
                "replaced %s nonfinite historical volume scalar(s) with unknown",
                repaired,
            )
    # The old incoming-metadata path also accepted nonphysical dimensions.
    # Retire only those unusable facts before enforcing the generated checks;
    # valid finite dimensions, including zero and tiny values, stay unchanged.
    for axis in ("bbox_x_mm", "bbox_y_mm", "bbox_z_mm"):
        dimension = table.c[axis]
        statement = (
            table.update()
            .where(sa.or_(dimension < 0, dimension > 1.7976931348623157e308))
            .values({axis: None})
        )
        if op.get_context().as_sql:
            op.execute(statement)
        else:
            repaired = op.get_bind().execute(statement).rowcount
            if repaired > 0:
                logging.getLogger("alembic.runtime.migration").warning(
                    "replaced %s invalid historical %s fact(s) with unknown",
                    repaired,
                    axis,
                )
    # One table copy adds the generated constraints and switches future defaults.
    with op.batch_alter_table(
        "metadata", schema=None, copy_from=_metadata_table(constrained=False)
    ) as batch_op:
        batch_op.alter_column(
            "volume_state",
            existing_type=sa.Text(),
            existing_nullable=False,
            server_default="not_calculated",
        )
        batch_op.alter_column(
            "volume_not_calculated_cause",
            existing_type=sa.Text(),
            existing_nullable=True,
            server_default="enrichment_pending",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_bbox_x_mm_physical"),
            "bbox_x_mm IS NULL OR (bbox_x_mm >= 0 AND bbox_x_mm <= 1.7976931348623157e+308)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_bbox_y_mm_physical"),
            "bbox_y_mm IS NULL OR (bbox_y_mm >= 0 AND bbox_y_mm <= 1.7976931348623157e+308)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_bbox_z_mm_physical"),
            "bbox_z_mm IS NULL OR (bbox_z_mm >= 0 AND bbox_z_mm <= 1.7976931348623157e+308)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_volume_evidence"),
            "(volume_state = 'measured' AND volume_mm3 IS NOT NULL AND volume_mm3 > 0 AND volume_mm3 <= 1.7976931348623157e+308 AND volume_method IS NOT NULL AND volume_method = 'mesh_surface_integral' AND volume_unavailable_cause IS NULL AND volume_not_calculated_cause IS NULL) OR (volume_state = 'unavailable' AND volume_mm3 IS NULL AND volume_method IS NOT NULL AND volume_method = 'mesh_surface_integral' AND volume_unavailable_cause IS NOT NULL AND volume_not_calculated_cause IS NULL) OR (volume_state = 'not_calculated' AND volume_mm3 IS NULL AND volume_method IS NULL AND volume_unavailable_cause IS NULL AND volume_not_calculated_cause IS NOT NULL) OR (volume_state = 'legacy_unassessed' AND (volume_mm3 IS NULL OR (volume_mm3 >= -1.7976931348623157e+308 AND volume_mm3 <= 1.7976931348623157e+308)) AND volume_method IS NULL AND volume_unavailable_cause IS NULL AND volume_not_calculated_cause IS NULL)",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_volume_method_values"),
            "volume_method IN ('mesh_surface_integral')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_volume_not_calculated_cause_values"),
            "volume_not_calculated_cause IN ('enrichment_pending', 'not_applicable', 'not_requested', 'geometry_unavailable', 'topology_not_evaluated')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_volume_state_values"),
            "volume_state IN ('measured', 'unavailable', 'not_calculated', 'legacy_unassessed')",
        )
        batch_op.create_check_constraint(
            batch_op.f("ck_metadata_volume_unavailable_cause_values"),
            "volume_unavailable_cause IN ('not_watertight', 'inconsistent_winding', 'non_positive_integral', 'nonfinite_integral', 'measurement_failed')",
        )

    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table(
        "metadata", schema=None, copy_from=_metadata_table(constrained=True)
    ) as batch_op:
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_bbox_z_mm_physical"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_bbox_y_mm_physical"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_bbox_x_mm_physical"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_volume_unavailable_cause_values"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_volume_state_values"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_volume_not_calculated_cause_values"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_volume_method_values"), type_="check"
        )
        batch_op.drop_constraint(
            batch_op.f("ck_metadata_volume_evidence"), type_="check"
        )
        batch_op.drop_column("volume_not_calculated_cause")
        batch_op.drop_column("volume_unavailable_cause")
        batch_op.drop_column("volume_method")
        batch_op.drop_column("volume_state")

    # ### end Alembic commands ###
