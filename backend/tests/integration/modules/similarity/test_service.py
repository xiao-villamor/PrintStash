"""Public progress and cached evidence stay bounded by the requesting editor."""

import json

import pytest

from app.core.errors import OperationError
from app.db.models import CollectionRole
from app.modules.similarity import configuration, service


@pytest.fixture
def cached_pair(
    make_user,
    make_model,
    make_file,
    make_geometry_fingerprint,
    make_similarity_candidate,
    make_similarity_observation,
):
    actor = make_user(superuser=True)
    a, b = make_model(), make_model()
    left = make_geometry_fingerprint(make_file(a), state="ready")
    right = make_geometry_fingerprint(make_file(b), state="ready")
    row = make_similarity_candidate(a, b)
    make_similarity_observation(row, left, right)
    return actor, row, a, b


class TestModelSummary:
    def test_counts_current_open_candidates(self, db_session, cached_pair):
        actor, _, a, _ = cached_pair

        assert service.model_summary(db_session, actor, a.id) == {
            "open_candidates": 1,
            "confirmed": 0,
        }

    def test_counts_confirmed_evidence(self, db_session, cached_pair):
        actor, row, a, _ = cached_pair
        row.review_state = "confirmed"
        db_session.add(row)
        db_session.commit()

        assert service.model_summary(db_session, actor, a.id) == {
            "open_candidates": 0,
            "confirmed": 1,
        }

    def test_denies_hidden_model(self, db_session, cached_pair, make_user):
        _, _, a, _ = cached_pair

        with pytest.raises(OperationError, match="similarity_scope_unavailable"):
            service.model_summary(db_session, make_user(), a.id)


class TestQueryModel:
    def test_returns_cached_evidence_before_analysis(self, db_session, cached_pair):
        actor, row, a, _ = cached_pair
        configuration.update_settings(db_session, actor, {"enabled": True})

        result = service.query_model(db_session, actor, a.id)

        assert [item["id"] for item in result["items"]] == [row.id]
        assert result["run"]["scope_ids"] == [a.id]
        assert result["run"]["state"] == "queued"

    def test_reuses_the_active_model_run(self, db_session, cached_pair):
        actor, _, a, _ = cached_pair
        configuration.update_settings(db_session, actor, {"enabled": True})
        first = service.query_model(db_session, actor, a.id)

        assert (
            service.query_model(db_session, actor, a.id)["run"]["id"]
            == first["run"]["id"]
        )

    def test_reports_disabled_analysis(self, db_session, cached_pair):
        actor, _, a, _ = cached_pair

        with pytest.raises(OperationError, match="similarity_disabled"):
            service.query_model(db_session, actor, a.id)


class TestListRuns:
    def test_paginates_history(self, db_session, make_user, make_similarity_run):
        actor = make_user(superuser=True)
        older = make_similarity_run(actor, active=False)
        newer = make_similarity_run(actor, active=False)

        first = service.list_runs(db_session, actor, limit=1)
        second = service.list_runs(
            db_session, actor, limit=1, before_id=first["next_cursor"]
        )

        assert [row["id"] for row in first["items"]] == [newer.id]
        assert [row["id"] for row in second["items"]] == [older.id]
        assert second["next_cursor"] is None

    def test_hides_other_editors_runs(
        self,
        db_session,
        make_user,
        make_similarity_run,
        make_model,
        make_collection,
        grant_role,
    ):
        collection = make_collection()
        model = make_model(collection=collection)
        editor = make_user()
        grant_role(editor, collection, CollectionRole.EDIT)
        own = make_similarity_run(editor, model_ids=(model.id,), active=False)
        make_similarity_run(make_user(superuser=True), active=False)

        assert [
            row["id"] for row in service.list_runs(db_session, editor)["items"]
        ] == [own.id]

    def test_hides_history_after_permission_loss(
        self, db_session, make_user, make_model, make_similarity_run
    ):
        actor = make_user()
        make_similarity_run(actor, model_ids=(make_model().id,), active=False)

        assert service.list_runs(db_session, actor) == {
            "items": [],
            "next_cursor": None,
        }

    def test_omits_worker_ownership_details(self, make_user, make_similarity_run):
        run = make_similarity_run(
            make_user(superuser=True),
            checkpoint_json=json.dumps({"file_id": 10, "pending_pairs": [[1, 2]]}),
            lease_token="private-worker-token",
        )

        result = service.project_run(run)

        assert result["checkpoint"] == {"file_id": 10}
        assert not {
            "lease_token",
            "settings_json",
            "active_scope_key",
            "lease_expires_at",
        }.intersection(result)


class TestStatus:
    def test_counts_only_current_pending_inputs(
        self, db_session, make_user, make_model, make_file, make_geometry_fingerprint
    ):
        actor = make_user(superuser=True)
        make_geometry_fingerprint(make_file(make_model()))
        make_geometry_fingerprint(make_file(make_model()), source_sha256="a" * 64)
        make_geometry_fingerprint(make_file(make_model(trashed=True)))

        assert service.status(db_session, actor)["pending_fingerprints"] == 1

    def test_hides_administrator_settings(self, db_session, make_user):
        result = service.status(db_session, make_user())

        assert result["settings"] is None
        assert result["selection"] == {"minimum_confidence": 0.9, "class_overrides": {}}
