"""Mesh volume evidence is durable independently of optional fingerprints."""

import pytest
from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMethod,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeState,
    VolumeUnavailableCause,
)
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from app.db.models import (
    ArtifactDerivative,
    DerivativeKind,
    DerivativeState,
    JobKind,
    Metadata,
)
from app.modules.derivatives import producers
from tests.factories import content


class TestVolumePersistence:
    def test_new_mesh_metadata_starts_pending(self, stored, make_metadata):
        artifact = stored("cube.stl", content.binary_stl())
        metadata = make_metadata(artifact)

        assert metadata.volume_state is VolumeState.NOT_CALCULATED
        assert metadata.volume_method is None
        assert metadata.volume_unavailable_cause is None
        assert (
            metadata.volume_not_calculated_cause
            is VolumeNotCalculatedCause.ENRICHMENT_PENDING
        )
        assert metadata.volume_mm3 is None

    def test_persists_unavailable_volume_without_fingerprint(
        self, db_session, stored, make_derivative, monkeypatch
    ):
        import trimesh

        from app.modules.ingestion import extensions

        mesh = trimesh.creation.box(extents=[10, 10, 10])
        mesh.faces[0] = mesh.faces[0][::-1]
        artifact = stored("inconsistent.stl", mesh.export(file_type="stl"))
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        monkeypatch.setattr(extensions, "extraction_options", lambda _sessions: {})

        outcome = producers.derive_mesh(artifact.id)

        db_session.expire_all()
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        assert outcome.kinds[DerivativeKind.METADATA] is DerivativeState.READY
        assert metadata.volume_mm3 is None
        assert metadata.volume_state is VolumeState.UNAVAILABLE
        assert metadata.volume_method is VolumeMethod.MESH_SURFACE_INTEGRAL
        assert (
            metadata.volume_unavailable_cause
            is VolumeUnavailableCause.INCONSISTENT_WINDING
        )
        assert metadata.volume_not_calculated_cause is None


class TestVolumeDatabaseConstraint:
    @pytest.mark.parametrize(
        ("state", "value", "cause"),
        [
            (VolumeState.MEASURED, 1.0, None),
            (
                VolumeState.UNAVAILABLE,
                None,
                VolumeUnavailableCause.NOT_WATERTIGHT.value,
            ),
        ],
        ids=["measured-missing-method", "unavailable-missing-method"],
    )
    def test_rejects_missing_integral_method(
        self, db_session, stored, make_metadata, state, value, cause
    ):
        artifact = stored("cube.stl", content.binary_stl())
        metadata = make_metadata(artifact)
        parameters = {
            "state": state.value,
            "value": value,
            "cause": cause,
            "id": metadata.id,
        }
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(
                    "UPDATE metadata SET volume_state=:state, volume_mm3=:value, "
                    "volume_method=NULL, volume_unavailable_cause=:cause, "
                    "volume_not_calculated_cause=NULL WHERE id=:id"
                ),
                parameters,
            )
        db_session.rollback()

    @pytest.mark.parametrize(
        ("state", "value", "method", "unavailable", "not_calculated"),
        [
            ("measured", None, "mesh_surface_integral", None, None),
            ("measured", 0.0, "mesh_surface_integral", None, None),
            ("measured", -1.0, "mesh_surface_integral", None, None),
            ("measured", float("inf"), "mesh_surface_integral", None, None),
            ("measured", float("nan"), "mesh_surface_integral", None, None),
            ("measured", 1.0, "mesh_surface_integral", "not_watertight", None),
            ("measured", 1.0, "mesh_surface_integral", None, "enrichment_pending"),
            ("unavailable", None, "mesh_surface_integral", None, None),
            ("unavailable", 0.0, "mesh_surface_integral", "not_watertight", None),
            (
                "unavailable",
                None,
                "mesh_surface_integral",
                "not_watertight",
                "enrichment_pending",
            ),
            ("not_calculated", None, None, None, None),
            (
                "not_calculated",
                None,
                "mesh_surface_integral",
                None,
                "enrichment_pending",
            ),
            ("not_calculated", 0.0, None, None, "enrichment_pending"),
            ("not_calculated", None, None, "not_watertight", "enrichment_pending"),
            ("legacy_unassessed", 1.0, "mesh_surface_integral", None, None),
            ("legacy_unassessed", None, None, "not_watertight", None),
            ("legacy_unassessed", None, None, None, "enrichment_pending"),
            ("legacy_unassessed", float("inf"), None, None, None),
            ("legacy_unassessed", float("-inf"), None, None, None),
            ("unknown", None, None, None, "enrichment_pending"),
            (None, None, None, None, "enrichment_pending"),
            ("unavailable", None, "unknown", "not_watertight", None),
            ("unavailable", None, "mesh_surface_integral", "unknown", None),
            ("not_calculated", None, None, None, "unknown"),
        ],
    )
    def test_rejects_invalid_cross_column_volume_evidence(
        self,
        db_session,
        stored,
        make_metadata,
        state,
        value,
        method,
        unavailable,
        not_calculated,
    ):
        artifact = stored("cube.stl", content.binary_stl())
        metadata = make_metadata(artifact)
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(
                    "UPDATE metadata SET volume_state=:state,volume_mm3=:value,volume_method=:method,volume_unavailable_cause=:unavailable,volume_not_calculated_cause=:not_calculated WHERE id=:id"
                ),
                {
                    "state": state,
                    "value": value,
                    "method": method,
                    "unavailable": unavailable,
                    "not_calculated": not_calculated,
                    "id": metadata.id,
                },
            )
        db_session.rollback()


class TestVolumeMetadataFactory:
    @pytest.mark.parametrize(
        "column",
        [
            "volume_state",
            "volume_method",
            "volume_unavailable_cause",
            "volume_not_calculated_cause",
        ],
    )
    def test_rejects_internal_volume_column_overrides(
        self, stored, make_metadata, column
    ):
        artifact = stored("cube.stl", content.binary_stl())
        with pytest.raises(ValueError, match="volume"):
            make_metadata(artifact, **{column: "invalid"})


class TestVolumeRecipeRefresh:
    def test_refreshes_gcode_metadata_from_scalar_recipe_without_losing_slicer_facts(
        self, db_session, stored, make_metadata, make_derivative
    ):
        from app.core.time import utcnow
        from app.modules.derivatives.kinds import group
        from app.modules.derivatives.source import DerivativeSource, subject_key
        from app.modules.library.volume_metadata import read_volume

        data = (
            content.gcode()
            + b"; estimated printing time (normal mode) = 1h 2m 3s\n; filament used [g] = 12.5\n"
        )
        artifact = stored("ordinary.gcode", data)
        make_metadata(artifact, volume=VolumeLegacyUnassessed(500.0))
        make_derivative(artifact, DerivativeKind.METADATA, recipe_version=1)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        source = DerivativeSource(group(JobKind.DERIVATIVES_GCODE))
        assert [
            item.subject_key
            for item in source.pending(db_session, now=utcnow(), limit=10)
        ] == [subject_key(artifact.id)]
        outcome = producers.derive_gcode(artifact.id)
        db_session.expire_all()
        metadata = db_session.exec(
            select(Metadata).where(Metadata.file_id == artifact.id)
        ).one()
        row = db_session.exec(
            select(ArtifactDerivative).where(
                ArtifactDerivative.file_id == artifact.id,
                ArtifactDerivative.kind == DerivativeKind.METADATA,
                ArtifactDerivative.recipe_version == 2,
            )
        ).one()
        assert outcome.kinds == {DerivativeKind.METADATA: DerivativeState.READY}
        assert read_volume(metadata) == VolumeNotCalculated(
            VolumeNotCalculatedCause.NOT_APPLICABLE
        )
        assert metadata.estimated_time_s == 3723
        assert metadata.filament_weight_g == 12.5
        assert metadata.slicer_name == "PrusaSlicer"
        assert row.recipe_version == 2
        assert source.pending(db_session, now=utcnow(), limit=10) == []

    def test_discovers_mesh_metadata_from_previous_evidence_recipe(
        self, db_session, stored, make_metadata, make_derivative
    ):
        from app.core.time import utcnow
        from app.modules.derivatives.kinds import MESH_GEOMETRY_RECIPE, group
        from app.modules.derivatives.source import DerivativeSource, subject_key

        artifact = stored("old-metadata.stl", content.binary_stl())
        make_metadata(artifact, volume=VolumeLegacyUnassessed(500.0))
        previous = make_derivative(artifact, DerivativeKind.METADATA, recipe_version=8)
        make_derivative(artifact, DerivativeKind.THUMBNAIL)
        source = DerivativeSource(group(JobKind.DERIVATIVES_MESH))
        assert previous.recipe_version < MESH_GEOMETRY_RECIPE
        assert previous.recipe_version == 8
        assert previous.state is DerivativeState.READY
        assert [
            item.subject_key
            for item in source.pending(db_session, now=utcnow(), limit=10)
        ] == [subject_key(artifact.id)]


class TestDimensionDatabaseConstraint:
    @pytest.mark.parametrize("axis", ["bbox_x_mm", "bbox_y_mm", "bbox_z_mm"])
    @pytest.mark.parametrize("value", [float("inf"), float("-inf"), -1.0])
    def test_rejects_nonphysical_persisted_dimensions(
        self, db_session, stored, make_metadata, axis, value
    ):
        artifact = stored("dimensions.stl", content.binary_stl())
        metadata = make_metadata(artifact)
        with pytest.raises(IntegrityError):
            db_session.execute(
                text(f"UPDATE metadata SET {axis}=:value WHERE id=:id"),
                {"value": value, "id": metadata.id},
            )
        db_session.rollback()
