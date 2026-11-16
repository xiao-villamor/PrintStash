"""Related profiles preserve Artifact identity without exposing inaccessible or trashed content."""

import pytest

from app.core.time import utcnow
from app.db.models import CollectionRole
from app.modules.library.model_views.profiles import related_profiles
from tests.factories import grant_collection_role


@pytest.fixture
def profile_context(
    db_session,
    make_user,
    make_collection,
    make_model,
    make_file,
    make_provenance_source,
    make_capture,
    make_artifact_link,
):
    user = make_user()
    visible = make_collection("Visible profiles")
    private = make_collection("Private profiles")
    grant_collection_role(db_session, user, visible, CollectionRole.VIEW)
    records = []
    for collection in (visible, visible, private):
        model = make_model(collection=collection)
        source = make_provenance_source(model, identity_key="a" * 64)
        capture = make_capture(
            source,
            snapshot={
                "profiles": {
                    "package": {
                        "id": "package",
                        "name": "Creator profile",
                        "origin": "provider",
                        "estimated_seconds": 100,
                    }
                },
                "private_note": "not-public",
            },
        )
        file = make_file(model)
        make_artifact_link(
            file, source, capture_id=capture.id, source_file_id="package"
        )
        records.append((model, file))
    return user, records


class TestRelatedProfiles:
    def test_binds_each_profile_to_its_original_artifact(
        self, db_session, profile_context
    ):
        user, records = profile_context

        result = related_profiles(db_session, user, records[0][0].id)

        assert [
            (item.model_id, item.file_id, item.profile.id) for item in result.items
        ] == [
            (records[0][0].id, records[0][1].id, "package"),
            (records[1][0].id, records[1][1].id, "package"),
        ]
        assert result.has_more is False

    def test_omits_inaccessible_publication_profiles(self, db_session, profile_context):
        user, records = profile_context

        result = related_profiles(db_session, user, records[2][0].id)

        assert result.items == []

    def test_omits_a_trashed_sibling_model(self, db_session, profile_context):
        user, records = profile_context
        records[1][0].deleted_at = utcnow()
        db_session.add(records[1][0])
        db_session.commit()

        result = related_profiles(db_session, user, records[0][0].id)

        assert [item.file_id for item in result.items] == [records[0][1].id]

    def test_omits_a_trashed_artifact(self, db_session, profile_context):
        user, records = profile_context
        records[1][1].deleted_at = utcnow()
        db_session.add(records[1][1])
        db_session.commit()

        result = related_profiles(db_session, user, records[0][0].id)

        assert [item.file_id for item in result.items] == [records[0][1].id]

    def test_projects_only_public_profile_fields(self, db_session, profile_context):
        user, records = profile_context

        result = related_profiles(db_session, user, records[0][0].id)

        assert result.items[0].profile.origin == "provider"
        assert result.items[0].profile.estimated_seconds == 100
        assert "not-public" not in result.model_dump_json()
