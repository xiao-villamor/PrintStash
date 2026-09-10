"""Indexed discovery is bounded, permission filtered and tolerant of repairs."""

import struct

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.db.models import CollectionRole, File, GeometryFingerprint
from app.modules.similarity import retrieval
from tests.factories import build_geometry_fingerprint


@pytest.fixture
def source(db_session, make_model, make_file):
    return build_geometry_fingerprint(
        db_session,
        make_file(make_model()),
        state="ready",
        normalized_area=1,
        area_volume_ratio=3,
        hull_ratio=1,
        euler_characteristic=2,
        d2_blob=struct.pack("<64f", *([1 / 64] * 64)),
    )


class TestRetrieval:
    def test_retrieves_repaired_topology(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        source,
        make_geometry_fingerprint,
    ):
        repaired = make_geometry_fingerprint(
            make_file(make_model()),
            state="ready",
            normalized_area=1.05,
            euler_characteristic=-8,
            watertight=False,
            d2_blob=source.d2_blob,
        )
        result = retrieval.find_candidates(
            db_session, source, make_user(superuser=True)
        )
        assert result.fingerprint_ids == (repaired.id,)

    def test_retains_quarter_face_decimation(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        source,
        make_geometry_fingerprint,
    ):
        source.face_count = 1000
        db_session.add(source)
        db_session.commit()
        remesh = make_geometry_fingerprint(
            make_file(make_model()),
            state="ready",
            face_count=250,
            normalized_area=1.05,
            d2_blob=source.d2_blob,
        )
        assert retrieval.find_candidates(
            db_session, source, make_user(superuser=True)
        ).fingerprint_ids == (remesh.id,)

    def test_bounds_primitive_buckets(
        self,
        db_session,
        make_model,
        make_file,
        make_user,
        source,
        make_geometry_fingerprint,
    ):
        for _ in range(25):
            make_geometry_fingerprint(
                make_file(make_model()),
                state="ready",
                normalized_area=1,
                d2_blob=source.d2_blob,
            )
        result = retrieval.find_candidates(
            db_session, source, make_user(superuser=True), limit=3, bucket_limit=10
        )
        assert len(result.fingerprint_ids) == 3
        assert result.examined == 10
        assert result.skipped_by_budget >= 8

    def test_uses_eight_indexed_hash_lookups(self, db_session, source):
        for group in ("physical", "normalized"):
            for index in range(4):
                setattr(source, f"{group}_hash_{index}", "0" * 64)
        statement = retrieval.hash_lookup(source).compile(
            db_session.get_bind(), compile_kwargs={"literal_binds": True}
        )
        plan = db_session.exec(text("EXPLAIN QUERY PLAN " + str(statement))).all()
        details = "\n".join(str(row) for row in plan)
        for group in ("physical", "normalized"):
            for index in range(4):
                assert f"ix_geometry_fingerprints_{group}_hash_{index}" in details
        assert "SCAN geometry_fingerprints" not in details

    @pytest.mark.parametrize(
        "column,index_name",
        [
            ("normalized_area", "ix_geometry_fingerprint_repair"),
            ("area_volume_ratio", "ix_geometry_fingerprint_strict"),
        ],
    )
    def test_uses_invariant_range_index(self, db_session, source, column, index_name):
        query = select(GeometryFingerprint.id).where(
            GeometryFingerprint.algorithm_version == source.algorithm_version,
            GeometryFingerprint.state == "ready",
            getattr(GeometryFingerprint, column).between(0.9, 1.1),
        )
        sql = query.compile(
            db_session.get_bind(), compile_kwargs={"literal_binds": True}
        )
        plan = db_session.exec(text("EXPLAIN QUERY PLAN " + str(sql))).all()
        assert index_name in str(plan)

    def test_excludes_hidden_target(
        self,
        db_session,
        source,
        make_model,
        make_file,
        make_user,
        make_collection,
        grant_role,
        make_geometry_fingerprint,
    ):
        collection = make_collection()
        source_file = db_session.get(File, source.file_id)
        source_file.model.collection_id = collection.id
        db_session.add(source_file.model)
        db_session.commit()
        make_geometry_fingerprint(
            make_file(make_model()),
            state="ready",
            normalized_area=1,
            d2_blob=source.d2_blob,
        )
        editor = make_user()
        grant_role(editor, collection, CollectionRole.EDIT)
        result = retrieval.find_candidates(db_session, source, editor)
        assert result.fingerprint_ids == ()
        assert result.examined == result.skipped_by_budget == 0

    def test_excludes_changed_target(
        self,
        db_session,
        source,
        make_model,
        make_file,
        make_user,
        make_geometry_fingerprint,
    ):
        file = make_file(make_model())
        make_geometry_fingerprint(
            file, state="ready", normalized_area=1, d2_blob=source.d2_blob
        )
        file.sha256 = "f" * 64
        db_session.add(file)
        db_session.commit()
        assert (
            retrieval.find_candidates(
                db_session, source, make_user(superuser=True)
            ).fingerprint_ids
            == ()
        )

    def test_applies_inertia_window_to_strict_lane(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        source,
        make_geometry_fingerprint,
    ):
        source.normalized_area = None
        source.inertia_ratio_0 = 0.4
        source.inertia_ratio_1 = 0.7
        db_session.add(source)
        db_session.commit()
        matching = make_geometry_fingerprint(
            make_file(make_model()),
            state="ready",
            area_volume_ratio=3,
            hull_ratio=1,
            euler_characteristic=2,
            inertia_ratio_0=0.41,
            inertia_ratio_1=0.7,
            d2_blob=source.d2_blob,
        )
        make_geometry_fingerprint(
            make_file(make_model()),
            state="ready",
            area_volume_ratio=3,
            hull_ratio=1,
            euler_characteristic=2,
            inertia_ratio_0=0.8,
            inertia_ratio_1=0.9,
            d2_blob=source.d2_blob,
        )
        assert retrieval.find_candidates(
            db_session, source, make_user(superuser=True)
        ).fingerprint_ids == (matching.id,)
