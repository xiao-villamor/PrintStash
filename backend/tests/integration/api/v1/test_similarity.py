"""Public analysis controls enforce scope and do not reveal hidden candidates."""

import pytest
from sqlmodel import select

from app.db.models import SimilarityRun


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
