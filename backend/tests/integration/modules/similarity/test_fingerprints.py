"""Only the current input lease may publish analysis, even across sessions."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta

import pytest
from printstash_core.mesh.similarity.components import ExpandedScene
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import File, GeometryFingerprint
from app.modules.media.fingerprints import (
    FingerprintResult,
    FingerprintResultState,
    extract,
)
from app.modules.media.mesh_facts import FingerprintFailureCode, SampledGeometry
from app.modules.media.mesh_resources import PreparedMesh, prepare_loaded_mesh
from app.modules.similarity import fingerprints
from tests.factories.geometry import tetrahedron


@pytest.fixture
def extracted():
    return extract(prepare_loaded_mesh(tetrahedron(), file_type="stl"))


class TestFingerprintLeases:
    @pytest.mark.parametrize("state", ["ready", "partial", "failed"])
    @pytest.mark.parametrize(
        "previous_version", ["geometry-v5-sh5f4577c4", "geometry-v6-sh5f4577c4"]
    )
    def test_recomputes_fingerprints_from_the_previous_stl_sample_recipe(
        self,
        db_session,
        make_model,
        make_file,
        make_geometry_fingerprint,
        state,
        previous_version,
    ):
        file = make_file(make_model())
        previous = make_geometry_fingerprint(
            file, state=state, algorithm_version=previous_version
        )

        claimed = fingerprints.claim(db_session, file)

        assert claimed is not None
        assert claimed[0] != previous.id
        db_session.refresh(previous)
        assert previous.algorithm_version == previous_version
        assert previous.state == state

    @pytest.mark.parametrize("state", ["ready", "partial", "failed"])
    def test_recomputes_fingerprints_from_the_python_hull_recipe(
        self, db_session, make_model, make_file, make_geometry_fingerprint, state
    ):
        file = make_file(make_model())
        previous = make_geometry_fingerprint(
            file, state=state, algorithm_version="geometry-v3-sh5f4577c4"
        )

        claimed = fingerprints.claim(db_session, file)

        assert claimed is not None
        assert claimed[0] != previous.id
        db_session.refresh(previous)
        assert previous.algorithm_version == "geometry-v3-sh5f4577c4"
        assert previous.state == state

    def test_recomputes_fingerprints_from_the_previous_view_recipe(
        self, db_session, make_model, make_file, make_geometry_fingerprint
    ):
        file = make_file(make_model())
        previous = make_geometry_fingerprint(
            file, state="ready", algorithm_version="geometry-v2-sh5f4577c4"
        )

        claimed = fingerprints.claim(db_session, file)

        assert claimed is not None
        assert claimed[0] != previous.id

    def test_coalesces_overlapping_scope_work(self, db_session, make_model, make_file):
        file = make_file(make_model())
        first = fingerprints.claim(db_session, file)

        with Session(db_session.get_bind()) as second:
            other_file = second.get(File, file.id)
            duplicate = fingerprints.claim(second, other_file)

        assert first is not None
        assert duplicate is None
        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 1

    def test_resumes_after_expired_lease(
        self, db_session, make_model, make_file, extracted
    ):
        file = make_file(make_model())
        first = fingerprints.claim(db_session, file)
        row = db_session.get(GeometryFingerprint, first[0])
        row.lease_expires_at = utcnow() - timedelta(seconds=1)
        db_session.add(row)
        db_session.commit()

        with Session(db_session.get_bind()) as fresh:
            current_file = fresh.get(File, file.id)
            successor = fingerprints.claim(fresh, current_file)
            assert successor[1] != first[1]
            assert fingerprints.publish(
                fresh,
                current_file,
                extracted,
                fingerprint_id=successor[0],
                token=successor[1],
            )

        assert not fingerprints.publish(
            db_session, file, extracted, fingerprint_id=first[0], token=first[1]
        )
        db_session.expire_all()
        assert db_session.get(GeometryFingerprint, first[0]).attempts == 2

    def test_discards_publication_after_source_change(
        self, db_session, make_model, make_file, extracted
    ):
        file = make_file(make_model())
        claimed = fingerprints.claim(db_session, file)
        with Session(db_session.get_bind()) as writer:
            changed = writer.get(File, file.id)
            changed.sha256 = "f" * 64
            writer.add(changed)
            writer.commit()

        published = fingerprints.publish(
            db_session, file, extracted, fingerprint_id=claimed[0], token=claimed[1]
        )

        assert not published
        assert all(
            row.state != "ready" for row in db_session.exec(select(GeometryFingerprint))
        )

    def test_old_owner_cannot_release_successor(
        self, db_session, make_model, make_file
    ):
        file = make_file(make_model())
        claimed = fingerprints.claim(db_session, file)
        row = db_session.get(GeometryFingerprint, claimed[0])
        row.lease_token = "successor"
        db_session.add(row)
        db_session.commit()

        fingerprints.release(db_session, claimed[0], claimed[1])
        db_session.expire_all()

        assert (
            db_session.get(GeometryFingerprint, claimed[0]).lease_token == "successor"
        )

    @pytest.mark.parametrize("state", ["failed", "partial", "unsupported"])
    def test_retries_incomplete_analysis_only_when_requested(
        self, db_session, make_model, make_file, state
    ):
        file = make_file(make_model())
        claimed = fingerprints.claim(db_session, file)
        failed = (
            extract(
                PreparedMesh(
                    tetrahedron(),
                    ExpandedScene((), ()),
                    SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
                )
            )
            if state == "partial"
            else FingerprintResult(
                FingerprintResultState(state),
                failure_code=FingerprintFailureCode.INVALID_SOURCE,
            )
        )
        assert failed.state.value == state
        assert fingerprints.publish(
            db_session, file, failed, fingerprint_id=claimed[0], token=claimed[1]
        )

        assert fingerprints.claim(db_session, file) is None
        assert fingerprints.claim(db_session, file, retry_incomplete=True) is not None
        db_session.expire_all()
        assert db_session.get(GeometryFingerprint, claimed[0]).state == "pending"
        assert fingerprints.claim(db_session, file, retry_incomplete=True) is None

    def test_precomputed_analysis_rejects_trashed_input(
        self, db_session, make_model, make_file, extracted
    ):
        file = make_file(make_model(), trashed=True)

        result = fingerprints.publish_precomputed(db_session, file, extracted)

        assert result == "stale"
        assert db_session.exec(select(GeometryFingerprint)).all() == []


class TestFingerprintPublication:
    @pytest.mark.parametrize("state", list(FingerprintResultState))
    def test_persists_each_outcome_as_a_text_state(
        self, db_session, make_model, make_file, state, extracted
    ):
        file = make_file(make_model())
        if state is FingerprintResultState.READY:
            result = extracted
        elif state is FingerprintResultState.PARTIAL:
            result = extract(
                PreparedMesh(
                    tetrahedron(),
                    ExpandedScene((), ()),
                    SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
                )
            )
        else:
            result = FingerprintResult(
                state=state, failure_code=FingerprintFailureCode.INVALID_SOURCE
            )

        published = fingerprints.publish_precomputed(db_session, file, result)

        assert type(published) is str
        assert published == state.value
        db_session.expire_all()
        rows = db_session.exec(select(GeometryFingerprint)).all()
        assert rows
        assert all(type(row.state) is str for row in rows)
        assert all(row.state == state.value for row in rows)

    def test_persists_component_lineage(
        self, db_session, make_model, make_file, extracted
    ):
        file = make_file(make_model())

        state = fingerprints.publish_precomputed(db_session, file, extracted)

        assert state == "ready"
        rows = db_session.exec(
            select(GeometryFingerprint).order_by(GeometryFingerprint.component_index)
        ).all()
        assert [row.component_index for row in rows] == [0, 1]
        assert all(row.source_sha256 == file.sha256 for row in rows)
        assert all(row.volume == pytest.approx(1000) for row in rows)
        assert all(len(row.d2_blob) == len(row.sh_blob) == 256 for row in rows)
        assert all(row.lease_token is None for row in rows)

    def test_precomputed_publication_is_idempotent(
        self, db_session, make_model, make_file, extracted
    ):
        file = make_file(make_model())
        fingerprints.publish_precomputed(db_session, file, extracted)

        state = fingerprints.publish_precomputed(db_session, file, extracted)

        assert state == "ready"
        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 2


class TestPublicationBoundaries:
    def test_publishes_precomputed_analysis_in_caller_transaction(
        self, db_session, make_model, make_file, extracted
    ):
        from app.modules.ingestion.extensions import MeshFingerprintPublished

        model = make_model()
        original_name = model.name
        file = make_file(model)
        model.name = "Caller transaction update"
        db_session.add(model)
        db_session.flush()
        outcome = fingerprints.publish_precomputed_in_transaction(
            db_session, file, extracted
        )
        assert outcome == MeshFingerprintPublished(FingerprintResultState.READY)
        rows = db_session.exec(select(GeometryFingerprint)).all()
        assert [row.component_index for row in rows] == [0, 1]
        assert all(row.state == "ready" for row in rows)
        db_session.rollback()
        with Session(db_session.get_bind()) as fresh:
            assert fresh.exec(select(GeometryFingerprint)).all() == []
            assert fresh.get(File, file.id) is not None
            assert fresh.get(type(model), model.id).name == original_name

    def test_defers_precomputed_analysis_to_active_owner(
        self, db_session, make_model, make_file, extracted
    ):
        from app.core.time import ensure_utc
        from app.modules.ingestion.extensions import MeshFingerprintDeferred

        file = make_file(make_model())
        claimed = fingerprints.claim(db_session, file)
        row = db_session.get(GeometryFingerprint, claimed[0])
        expiry = ensure_utc(row.lease_expires_at)
        outcome = fingerprints.publish_precomputed_in_transaction(
            db_session, file, extracted
        )
        assert outcome == MeshFingerprintDeferred(expiry)
        db_session.refresh(row)
        assert row.lease_token == claimed[1]
        assert row.state == "pending"
        assert row.attempts == 1
        assert len(db_session.exec(select(GeometryFingerprint)).all()) == 1
        db_session.rollback()

    def test_reuses_complete_precomputed_analysis(
        self, db_session, make_model, make_file, extracted
    ):
        from app.modules.ingestion.extensions import MeshFingerprintPublished

        file = make_file(make_model())
        assert fingerprints.publish_precomputed(db_session, file, extracted) == "ready"
        before = [
            (row.id, row.attempts)
            for row in db_session.exec(select(GeometryFingerprint)).all()
        ]
        outcome = fingerprints.publish_precomputed_in_transaction(
            db_session, file, extracted
        )
        assert outcome == MeshFingerprintPublished(FingerprintResultState.READY)
        db_session.commit()
        with Session(db_session.get_bind()) as fresh:
            assert [
                (row.id, row.attempts)
                for row in fresh.exec(select(GeometryFingerprint)).all()
            ] == before

    def test_refuses_changed_precomputed_algorithm(
        self, db_session, make_model, make_file, extracted
    ):
        file = make_file(make_model())
        changed = replace(extracted, algorithm_version="different-recipe")
        with pytest.raises(
            ValueError, match="precomputed_fingerprint_algorithm_changed"
        ):
            fingerprints.publish_precomputed_in_transaction(db_session, file, changed)
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    def test_rejects_detached_publication_authority(self, db_session, extracted):
        from app.modules.ingestion.extensions import MeshFingerprintRejected
        from tests.factories.library import detached_file

        file = detached_file()
        assert (
            fingerprints.publish_precomputed_in_transaction(db_session, file, extracted)
            == MeshFingerprintRejected()
        )
        assert not fingerprints.publish_in_transaction(
            db_session, file, extracted, fingerprint_id=1, token="absent-authority"
        )
        assert db_session.exec(select(GeometryFingerprint)).all() == []

    @pytest.mark.parametrize("kind", ["oversized", "nonfinite"], ids=str)
    def test_rolls_back_invalid_descriptor_metadata(
        self, db_session, make_model, make_file, extracted, kind
    ):
        file = make_file(make_model())
        claimed = fingerprints.claim(db_session, file)
        values = deepcopy(extracted.records[0].values)
        values["recipe"] = {
            "invalid": "x" * fingerprints.JSON_LIMIT
            if kind == "oversized"
            else float("nan")
        }
        invalid = replace(
            extracted,
            records=(
                replace(extracted.records[0], values=values),
                *extracted.records[1:],
            ),
        )
        with pytest.raises(ValueError):
            fingerprints.publish(
                db_session, file, invalid, fingerprint_id=claimed[0], token=claimed[1]
            )
        with Session(db_session.get_bind()) as fresh:
            rows = fresh.exec(select(GeometryFingerprint)).all()
            assert len(rows) == 1
            assert rows[0].id == claimed[0]
            assert rows[0].state == "pending"
            assert rows[0].lease_token == claimed[1]
            assert rows[0].attempts == 1
            assert rows[0].metrics_json == "{}"
        assert fingerprints.publish(
            db_session, file, extracted, fingerprint_id=claimed[0], token=claimed[1]
        )
        with Session(db_session.get_bind()) as fresh:
            rows = fresh.exec(select(GeometryFingerprint)).all()
            assert len(rows) == 2
            assert all(row.state == "ready" for row in rows)
            assert all(row.lease_token is None for row in rows)
