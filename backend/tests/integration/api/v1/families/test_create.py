"""Families group independent Models through an authenticated relational API.

No ordinary grouping action may change an Artifact, Revision, print outcome or
source. Mixed permissions must reject a write before any relationship changes.
"""

import hashlib

import pytest
from sqlmodel import select

from app.db.models import FileType, ModelFamily, ModelFamilyMember
from tests.factories import build_external_library
from tests.paths import TESTDATA_DIR, require_fixtures

BENCHY = TESTDATA_DIR / "benchy" / "3dbenchy.stl"
require_fixtures(BENCHY)


class TestCreateFamily:
    def test_groups_read_only_source_models(
        self, client, auth_headers, db_session, make_model, make_file, tmp_path
    ):
        root = tmp_path / "read-only-library"
        root.mkdir()
        path = root / "benchy.stl"
        data = BENCHY.read_bytes()
        path.write_bytes(data)
        library = build_external_library(db_session, root, writeback_enabled=False)
        models = [make_model("Original Benchy"), make_model("Benchy print variant")]
        artifacts = [
            make_file(
                model,
                external=True,
                file_type=FileType.STL,
                filename=path.name,
                path=str(path),
                external_library_id=library.id,
                source_key=path.name,
                sha256=hashlib.sha256(data).hexdigest(),
                size_bytes=len(data),
            )
            for model in models
        ]
        before = [db_session.get(type(row), row.id).model_dump() for row in artifacts]
        path.chmod(0o444)
        root.chmod(0o555)
        stat = path.stat()
        try:
            created = client.post(
                "/api/v1/families",
                headers=auth_headers,
                json={
                    "name": "Source variants",
                    "canonical_model_id": models[0].id,
                    "members": [{"model_id": model.id} for model in models],
                },
            )
            assert created.status_code == 201, created.text
            assert created.json()["member_count"] == 2
            assert path.read_bytes() == data
            assert path.stat().st_mtime_ns == stat.st_mtime_ns
            db_session.expire_all()
            assert [
                db_session.get(type(row), row.id).model_dump() for row in artifacts
            ] == before
        finally:
            root.chmod(0o755)
            path.chmod(0o644)

    def test_creates_family_without_changing_members(
        self, client, auth_headers, db_session, make_model, make_file
    ):
        first, second = make_model("Original"), make_model("Print variant")
        revision_a = make_file(first, file_type=FileType.GCODE, recommended=True)
        revision_b = make_file(second, file_type=FileType.GCODE, recommended=True)
        snapshots = [
            db_session.get(type(row), row.id).model_dump()
            for row in (first, second, revision_a, revision_b)
        ]

        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Bracket variations",
                "canonical_model_id": first.id,
                "members": [
                    {"model_id": first.id},
                    {"model_id": second.id, "role": "print_variant"},
                ],
            },
        )
        db_session.expire_all()
        db_session.refresh(first)
        db_session.refresh(second)
        db_session.refresh(revision_a)
        db_session.refresh(revision_b)

        assert response.status_code == 201, response.text
        assert response.json()["canonical_model_id"] == first.id
        assert response.json()["member_count"] == 2
        assert [
            db_session.get(type(row), row.id).model_dump()
            for row in (first, second, revision_a, revision_b)
        ] == snapshots

    def test_rejects_duplicate_member_ids(
        self, client, auth_headers, db_session, make_model
    ):
        model = make_model()

        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Invalid",
                "canonical_model_id": model.id,
                "members": [{"model_id": model.id}, {"model_id": model.id}],
            },
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ModelFamily)).all() == []

    def test_rejects_empty_creation(self, client, auth_headers, db_session):
        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Invalid",
                "canonical_model_id": 1,
                "members": [],
            },
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ModelFamily)).all() == []

    def test_requires_explicit_canonical(
        self, client, auth_headers, db_session, make_model
    ):
        model = make_model()

        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Invalid",
                "members": [{"model_id": model.id}],
            },
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ModelFamilyMember)).all() == []

    def test_accepts_one_member_family(self, client, auth_headers, make_model):
        model = make_model()

        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Only variant",
                "canonical_model_id": model.id,
                "members": [{"model_id": model.id}],
            },
        )

        assert response.status_code == 201, response.text
        assert response.json()["member_count"] == 1
        assert response.json()["canonical_model_id"] == model.id

    def test_rejects_second_live_family(
        self,
        client,
        auth_headers,
        db_session,
        make_family,
        make_model,
        make_family_member,
    ):
        model = make_model()
        existing = make_family()
        member = make_family_member(existing, model, canonical=True)

        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Conflicting",
                "canonical_model_id": model.id,
                "members": [{"model_id": model.id}],
            },
        )

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == "family_membership_conflict"
        assert db_session.exec(select(ModelFamily.id)).all() == [existing.id]
        assert db_session.exec(select(ModelFamilyMember.id)).all() == [member.id]

    @pytest.mark.parametrize(
        "scale", [0, -1, "NaN", "Infinity"], ids=["zero", "negative", "nan", "infinity"]
    )
    def test_rejects_invalid_relative_scale(
        self, client, auth_headers, db_session, make_model, scale
    ):
        model = make_model()

        response = client.post(
            "/api/v1/families",
            headers=auth_headers,
            json={
                "name": "Invalid",
                "canonical_model_id": model.id,
                "members": [{"model_id": model.id, "scale_factor": scale}],
            },
        )

        assert response.status_code == 422, response.text
        assert db_session.exec(select(ModelFamilyMember)).all() == []
