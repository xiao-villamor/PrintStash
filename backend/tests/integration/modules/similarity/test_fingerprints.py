"""Only the current input lease may publish analysis, even across sessions."""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import File, GeometryFingerprint
from app.modules.media.fingerprints import FingerprintResult, extract
from app.modules.media.mesh_resources import prepare_loaded_mesh
from app.modules.similarity import fingerprints
from tests.factories.geometry import tetrahedron


@pytest.fixture
def extracted():
    return extract(prepare_loaded_mesh(tetrahedron(), file_type="stl"))


class TestFingerprintLeases:
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
        failed = FingerprintResult(state, failure_code="invalid_source")
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
