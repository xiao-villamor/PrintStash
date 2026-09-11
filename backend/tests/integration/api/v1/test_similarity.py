"""Public analysis controls enforce scope and do not reveal hidden candidates."""

import pytest
from sqlmodel import select

from app.db.models import SimilarityRun


@pytest.fixture
def current_pair(
    make_model,
    make_file,
    make_geometry_fingerprint,
    make_similarity_candidate,
    make_similarity_observation,
):
    first, second = make_model(), make_model()
    candidate = make_similarity_candidate(first, second)
    make_similarity_observation(
        candidate,
        make_geometry_fingerprint(make_file(first), state="ready"),
        make_geometry_fingerprint(make_file(second), state="ready"),
    )
    return candidate


class TestSimilarity:
    @pytest.mark.parametrize(
        "method,path,payload",
        [
            ("get", "/similarity/status", None),
            ("get", "/similarity/candidates", None),
            ("get", "/similarity/candidates/1", None),
            ("get", "/similarity/runs", None),
            ("get", "/similarity/runs/1", None),
            ("post", "/similarity/runs", {}),
            ("post", "/similarity/search", {"text": "cup"}),
            ("post", "/similarity/selection-preview", {}),
            ("post", "/similarity/runs/1/cancel", {}),
            (
                "post",
                "/similarity/candidates/1/decision",
                {"action": "reject", "version": 1, "request_id": "test"},
            ),
            ("patch", "/similarity/settings", {"enabled": True}),
            ("get", "/models/1/similar", None),
            ("post", "/models/1/similar/query", {}),
        ],
    )
    def test_requires_authentication(self, client, method, path, payload):
        kwargs = {} if payload is None else {"json": payload}
        assert client.request(method, "/api/v1" + path, **kwargs).status_code == 401

    def test_reports_standalone_capabilities(self, client, auth_headers):
        response = client.get("/api/v1/similarity/status", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["enabled"] is False
        assert response.json()["capabilities"]["family_resolution"] is False
        assert response.json()["pending_fingerprints"] == 0

    def test_admin_can_configure_start_cancel(self, client, auth_headers, db_session):
        settings = client.patch(
            "/api/v1/similarity/settings", json={"enabled": True}, headers=auth_headers
        )
        assert settings.status_code == 200
        response = client.post(
            "/api/v1/similarity/runs", json={"scope": "library"}, headers=auth_headers
        )
        assert response.status_code == 202, response.text
        run_id = response.json()["id"]
        assert "lease_token" not in response.json()
        assert (
            client.post(
                f"/api/v1/similarity/runs/{run_id}/cancel", headers=auth_headers
            ).status_code
            == 200
        )
        assert db_session.exec(select(SimilarityRun)).one().cancel_requested

    @pytest.mark.parametrize(
        "patch",
        [
            {"max_candidates": 101},
            {"sample_points": 5001},
            {"triangle_cap": 200001},
            {"minimum_confidence": 0.1},
            {"class_overrides": {"made_up": 0.9}},
            {"surprise": True},
        ],
    )
    def test_rejects_invalid_caps(self, client, auth_headers, patch):
        response = client.patch(
            "/api/v1/similarity/settings", json=patch, headers=auth_headers
        )
        assert response.status_code == 422
        assert (
            client.get("/api/v1/similarity/status", headers=auth_headers).json()[
                "enabled"
            ]
            is False
        )

    def test_nonadmin_cannot_change_settings(self, client, user_headers):
        response = client.patch(
            "/api/v1/similarity/settings",
            headers=user_headers(),
            json={"enabled": True},
        )
        assert response.status_code == 403

    def test_hidden_model_query_creates_no_run(
        self, client, user_headers, make_model, db_session
    ):
        model = make_model()
        response = client.post(
            f"/api/v1/models/{model.id}/similar/query", headers=user_headers()
        )
        assert response.status_code == 404
        assert db_session.exec(select(SimilarityRun)).all() == []

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"text": " "},
            {"text": "cup", "model_id": 1},
            {"text": "cup", "limit": 101},
            {"model_id": True},
        ],
    )
    def test_rejects_invalid_semantic_query(self, client, auth_headers, payload):
        response = client.post(
            "/api/v1/similarity/search", json=payload, headers=auth_headers
        )
        assert response.status_code == 422

    def test_rejects_disabled_semantic_search(self, client, auth_headers):
        response = client.post(
            "/api/v1/similarity/search", json={"text": "cup"}, headers=auth_headers
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "embedding_disabled"

    def test_previews_thresholds_without_starting_analysis(
        self,
        client,
        auth_headers,
        db_session,
        make_model,
        make_file,
        make_geometry_fingerprint,
        make_similarity_candidate,
        make_similarity_observation,
    ):

        a, b = make_model(), make_model()
        candidate = make_similarity_candidate(a, b, confidence=0.95)
        make_similarity_observation(
            candidate,
            make_geometry_fingerprint(make_file(a), state="ready"),
            make_geometry_fingerprint(make_file(b), state="ready"),
        )
        endpoint = "/api/v1/similarity/selection-preview"
        before = client.post(
            endpoint, json={"minimum_confidence": 0.99}, headers=auth_headers
        )
        assert before.json() == {"total": 0, "by_class": {}}
        after = client.post(
            endpoint,
            json={
                "minimum_confidence": 0.99,
                "class_overrides": {"identical_geometry": 0.9},
            },
            headers=auth_headers,
        )
        assert after.json() == {"total": 1, "by_class": {"identical_geometry": 1}}
        db_session.refresh(candidate)
        assert candidate.review_state == "open"
        assert candidate.version == 1
        assert db_session.exec(select(SimilarityRun)).all() == []


class TestPersistedResults:
    def test_reads_run_progress(self, client, auth_headers, app, monkeypatch):
        # Persisted work remains available when a process has no local wakeup.
        monkeypatch.delattr(app.state, "similarity_wakeup", raising=False)
        client.patch(
            "/api/v1/similarity/settings", json={"enabled": True}, headers=auth_headers
        ).raise_for_status()
        started = client.post("/api/v1/similarity/runs", json={}, headers=auth_headers)
        assert started.status_code == 202
        expected = started.json()
        listed = client.get("/api/v1/similarity/runs", headers=auth_headers)
        detail = client.get(
            f"/api/v1/similarity/runs/{expected['id']}", headers=auth_headers
        )

        assert listed.status_code == detail.status_code == 200
        assert listed.json() == {"items": [expected], "next_cursor": None}
        assert detail.json() == expected
        assert "lease_token" not in expected
        assert "active_scope_key" not in expected
        assert expected["state"] == "queued"

    def test_reads_comparison_evidence(self, client, auth_headers, current_pair):
        response = client.get(
            f"/api/v1/similarity/candidates/{current_pair.id}", headers=auth_headers
        )

        assert response.status_code == 200
        result = response.json()
        assert result["model_a"]["id"] == current_pair.model_a_id
        assert result["model_b"]["id"] == current_pair.model_b_id
        assert len(result["observations"]) == 1
        assert result["exact_equivalence"] is True

    def test_scopes_cached_candidates_to_model(
        self, client, auth_headers, current_pair, make_model, make_similarity_candidate
    ):
        make_similarity_candidate(make_model(), make_model())
        response = client.get(
            f"/api/v1/models/{current_pair.model_a_id}/similar", headers=auth_headers
        )

        assert response.status_code == 200
        assert [row["id"] for row in response.json()["items"]] == [current_pair.id]

    def test_schedules_model_query(
        self, client, auth_headers, current_pair, db_session
    ):
        client.patch(
            "/api/v1/similarity/settings", json={"enabled": True}, headers=auth_headers
        ).raise_for_status()
        response = client.post(
            f"/api/v1/models/{current_pair.model_a_id}/similar/query",
            headers=auth_headers,
        )

        assert response.status_code == 200
        result = response.json()
        assert [row["id"] for row in result["items"]] == [current_pair.id]
        assert result["run"]["scope"] == "models"
        assert result["run"]["scope_ids"] == [current_pair.model_a_id]
        assert db_session.exec(select(SimilarityRun)).one().id == result["run"]["id"]

    def test_refuses_semantic_work_during_maintenance(self, client, auth_headers):
        from app.runtime.maintenance import (
            end_restore_maintenance,
            hold_restore_maintenance,
        )

        hold_restore_maintenance()
        try:
            response = client.post(
                "/api/v1/similarity/search", json={"text": "cup"}, headers=auth_headers
            )
        finally:
            end_restore_maintenance()

        assert response.status_code == 503
        assert response.json()["detail"] == "restore_in_progress"


class TestRunScopeInput:
    @pytest.mark.parametrize(
        "invalid_id",
        [True, False, 1.5, "1", 0, -1, 2**63],
        ids=["true", "false", "fraction", "string", "zero", "negative", "overflow"],
    )
    def test_rejects_non_identifier_values(self, client, auth_headers, invalid_id):
        response = client.post(
            "/api/v1/similarity/runs",
            json={"scope": "models", "ids": [invalid_id]},
            headers=auth_headers,
        )

        assert response.status_code == 422, response.text


class TestIdentifierBounds:
    @pytest.mark.parametrize(
        "path",
        [
            "/similarity/runs/9223372036854775808",
            "/similarity/candidates/9223372036854775808",
            "/models/9223372036854775808/similar",
            "/similarity/runs?before_id=9223372036854775808",
            "/similarity/candidates?model_id=9223372036854775808",
            "/similarity/candidates?collection_id=9223372036854775808",
        ],
    )
    def test_refuses_identifiers_outside_database_range(
        self, client, auth_headers, path
    ):
        assert client.get("/api/v1" + path, headers=auth_headers).status_code == 422

    @pytest.mark.parametrize(
        "cursor",
        ["[true,1]", "[1,9223372036854775808]", "[1,true]", "[1,-1]", "invalid"],
    )
    def test_refuses_invalid_candidate_cursor(self, client, auth_headers, cursor):
        response = client.get(
            "/api/v1/similarity/candidates",
            params={"cursor": cursor},
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "similarity_cursor_invalid"

    @pytest.mark.parametrize("field", ["space_id", "model_id"])
    def test_refuses_oversized_embedding_identifiers(self, client, auth_headers, field):
        payload = {field: 2**63}
        if field == "space_id":
            payload["text"] = "cup"
        assert (
            client.post(
                "/api/v1/similarity/search", json=payload, headers=auth_headers
            ).status_code
            == 422
        )


class TestReviewIdentifiers:
    @pytest.mark.parametrize("field", ["version", "target_id", "collection_id"])
    @pytest.mark.parametrize("value", [True, "1", 2**63])
    def test_rejects_invalid_resolution_identifier(
        self, client, auth_headers, field, value
    ):
        payload = {
            "action": "create_multipart",
            "request_id": "invalid-id",
            "version": 1,
            field: value,
        }
        response = client.post(
            "/api/v1/similarity/candidates/1/decision",
            json=payload,
            headers=auth_headers,
        )
        assert response.status_code == 422
