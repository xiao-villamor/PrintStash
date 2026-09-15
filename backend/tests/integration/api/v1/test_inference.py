"""Only administrators can add probed endpoints; credentials stay encrypted and redacted."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlmodel import select

from app.db.models import AuditLog, IndexGeneration, InferenceEndpoint
from app.modules.inference.configuration import load

PROPOSAL = {
    "base_url": "http://inference.local:11434/v1",
    "model": "test-embedding",
    "kind": "embedding",
    "native_dimension": 4,
    "api_key": "test-api-key",
    "headers": {"X-Test-Token": "test-header-secret"},
}


@pytest.fixture
def embedding_endpoint():
    with patch(
        "app.modules.inference.remote.post_json",
        return_value={"data": [{"index": 0, "embedding": [1, 0, 0, 0]}]},
    ):
        yield


class TestCreateEndpoint:
    def test_rejects_an_unconfigured_environment_import(self, client, auth_headers):
        response = client.post(
            "/api/v1/config/ai-search/endpoints/from-environment/embedding",
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "inference_environment_unconfigured"

    def test_preserves_endpoint_credentials_when_editing_model(
        self, client, auth_headers, embedding_endpoint, db_session
    ):
        original = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        ).json()
        proposal = {
            key: value
            for key, value in PROPOSAL.items()
            if key not in {"api_key", "headers"}
        }
        response = client.post(
            "/api/v1/config/ai-search/endpoints",
            json=proposal
            | {"model": "new-model", "inherit_credentials_from_id": original["id"]},
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text
        endpoint = load(db_session.get(InferenceEndpoint, response.json()["id"]))
        assert endpoint.api_key.get_secret_value() == PROPOSAL["api_key"]
        assert endpoint.request_headers()["X-Test-Token"] == "test-header-secret"
        assert endpoint.identity != original["config_hash"]
        assert "test-api-key" not in response.text
        assert "test-header-secret" not in response.text

    @pytest.mark.parametrize(
        "url",
        [
            "http://another.local:11434/v1",
            "https://inference.local:11434/v1",
            "http://inference.local:11435/v1",
        ],
    )
    def test_rejects_credential_inheritance_across_origins(
        self, client, auth_headers, embedding_endpoint, db_session, url
    ):
        original = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        ).json()
        with patch(
            "app.modules.inference.remote.post_json",
            side_effect=AssertionError("unexpected egress"),
        ):
            response = client.post(
                "/api/v1/config/ai-search/endpoints",
                json={
                    "base_url": url,
                    "model": "new-model",
                    "native_dimension": 4,
                    "inherit_credentials_from_id": original["id"],
                },
                headers=auth_headers,
            )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "inference_credential_origin_changed"
        assert len(db_session.exec(select(InferenceEndpoint)).all()) == 1

    def test_clears_explicitly_replaced_credentials(
        self, client, auth_headers, embedding_endpoint, db_session
    ):
        original = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        ).json()
        response = client.post(
            "/api/v1/config/ai-search/endpoints",
            json=PROPOSAL
            | {
                "inherit_credentials_from_id": original["id"],
                "api_key": "",
                "headers": {},
            },
            headers=auth_headers,
        )
        assert response.status_code == 201, response.text
        assert response.json()["has_credentials"] is False
        assert (
            load(
                db_session.get(InferenceEndpoint, original["id"])
            ).api_key.get_secret_value()
            == "test-api-key"
        )

    def test_rejects_missing_credential_source(self, client, auth_headers):
        with patch(
            "app.modules.inference.remote.post_json",
            side_effect=AssertionError("unexpected egress"),
        ):
            response = client.post(
                "/api/v1/config/ai-search/endpoints",
                json=PROPOSAL | {"inherit_credentials_from_id": 999},
                headers=auth_headers,
            )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "inference_endpoint_unavailable"

    def test_reports_a_probed_endpoint(self, client, auth_headers, embedding_endpoint):
        response = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        )

        assert response.status_code == 201, response.text
        assert response.json() == {
            "id": 1,
            "kind": "embedding",
            "host": "inference.local",
            "base_url": "http://inference.local:11434/v1",
            "model": "test-embedding",
            "revision": "configured-v1",
            "model_repo": None,
            "mrl_dimensions": [],
            "config_hash": response.json()["config_hash"],
            "native_dimension": 4,
            "supports_images": False,
            "dialect": None,
            "guarantee": None,
            "has_credentials": True,
            "header_names": ["X-Test-Token"],
            "timeout_seconds": 15.0,
            "max_input_characters": 16384,
            "prefer_responses": False,
        }

    def test_keeps_inference_credentials_encrypted(
        self, client, auth_headers, embedding_endpoint, db_session
    ):
        response = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        )
        assert response.status_code == 201, response.text

        raw = db_session.exec(
            text("SELECT api_key, headers_json, config_json FROM inference_endpoints")
        ).one()
        assert "test-api-key" not in str(raw)
        assert "test-header-secret" not in str(raw)
        assert (
            load(
                db_session.get(InferenceEndpoint, response.json()["id"])
            ).request_headers()["X-Test-Token"]
            == "test-header-secret"
        )

    def test_omits_credentials_from_audit(
        self, client, auth_headers, embedding_endpoint, db_session
    ):
        response = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        )
        assert response.status_code == 201, response.text

        records = db_session.exec(
            select(AuditLog).where(AuditLog.action == "inference_endpoint_created")
        ).all()

        assert [json.loads(record.diff_json) for record in records] == [
            {"kind": "embedding", "host": "inference.local", "model": "test-embedding"}
        ]

    def test_requires_admin_endpoint_configuration(
        self, client, user_headers, db_session
    ):
        response = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=user_headers()
        )

        assert response.status_code == 403, response.text
        assert db_session.exec(select(InferenceEndpoint)).all() == []

    def test_isolates_endpoint_configuration_versions(
        self, client, auth_headers, embedding_endpoint, db_session
    ):
        first = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        )
        assert first.status_code == 201, first.text

        second = client.post(
            "/api/v1/config/ai-search/endpoints",
            json=PROPOSAL | {"api_key": "test-replacement-key"},
            headers=auth_headers,
        )

        assert second.status_code == 201, second.text
        assert first.json()["config_hash"] != second.json()["config_hash"]
        assert (
            load(
                db_session.get(InferenceEndpoint, first.json()["id"])
            ).api_key.get_secret_value()
            == "test-api-key"
        )

    def test_rejects_wrong_canary_dimension(
        self, client, auth_headers, embedding_endpoint, db_session
    ):
        response = client.post(
            "/api/v1/config/ai-search/endpoints",
            json=PROPOSAL | {"native_dimension": 8},
            headers=auth_headers,
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "embedding_dimension_mismatch"
        assert db_session.exec(select(InferenceEndpoint)).all() == []

    def test_redacts_invalid_configuration_input(self, client, auth_headers):
        response = client.post(
            "/api/v1/config/ai-search/endpoints",
            json=PROPOSAL | {"headers": {"Host": "test-secret-invalid-header"}},
            headers=auth_headers,
        )

        assert response.status_code == 422, response.text
        assert "test-api-key" not in response.text
        assert "test-secret-invalid-header" not in response.text

    def test_refuses_client_supplied_configuration_versions(self, client, auth_headers):
        response = client.post(
            "/api/v1/config/ai-search/endpoints",
            json=PROPOSAL | {"configuration_version": "a" * 32},
            headers=auth_headers,
        )

        assert response.status_code == 422, response.text


class TestReadSettings:
    def test_defaults_to_independent_opt_ins(self, client, auth_headers):
        response = client.get("/api/v1/config/ai-search", headers=auth_headers)

        assert response.status_code == 200, response.text
        assert response.json() == {
            "settings": {
                "enabled": False,
                "lexical_backend": "auto",
                "captions_enabled": False,
                "nl_filters_enabled": False,
                "local_models_enabled": False,
                "download_enabled": False,
                "send_rendered_images": False,
                "send_query_images": False,
                "timezone": "UTC",
                "sparse_expansion_enabled": False,
                "sparse_model_id": None,
                "chat_endpoint_id": None,
                "rollback_retention_hours": 24,
                "max_index_bytes": 2147483648,
                "query_timeout_seconds": 3.0,
                "semantic_floor": 0.35,
                "lexical_weight": 1.0,
                "semantic_weight": 1.0,
                "rrf_k": 60,
                "semantic_floors": {},
            },
            "endpoints": [],
            "environment_endpoints": [],
        }

    def test_discloses_hosts_without_credentials(
        self, client, auth_headers, embedding_endpoint
    ):
        created = client.post(
            "/api/v1/config/ai-search/endpoints", json=PROPOSAL, headers=auth_headers
        )
        assert created.status_code == 201, created.text

        response = client.get("/api/v1/config/ai-search", headers=auth_headers)

        assert response.json()["endpoints"][0]["host"] == "inference.local"
        assert "test-api-key" not in response.text
        assert "test-header-secret" not in response.text

        public_health = client.get("/api/v1/health")
        assert public_health.status_code == 200, public_health.text
        for private in (
            "inference.local",
            "11434",
            "test-api-key",
            "test-header-secret",
        ):
            assert private not in public_health.text

    def test_hides_administrative_hosts_from_regular_users(self, client, user_headers):
        response = client.get("/api/v1/config/ai-search", headers=user_headers())

        assert response.status_code == 403, response.text


class TestUpdateSettings:
    @pytest.mark.parametrize("kind", ["missing", "embedding"])
    def test_rejects_an_unusable_chat_endpoint(
        self, client, auth_headers, make_inference_endpoint, kind
    ):
        endpoint_id = make_inference_endpoint().id if kind == "embedding" else 999999
        response = client.put(
            "/api/v1/config/ai-search",
            headers=auth_headers,
            json={"chat_endpoint_id": endpoint_id},
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "inference_chat_unavailable"
        saved = client.get("/api/v1/config/ai-search", headers=auth_headers)
        assert saved.json()["settings"]["chat_endpoint_id"] is None

    def test_rejects_an_unavailable_sparse_model(self, client, auth_headers):
        response = client.put(
            "/api/v1/config/ai-search",
            headers=auth_headers,
            json={
                "local_models_enabled": True,
                "sparse_expansion_enabled": True,
                "sparse_model_id": "f" * 64,
            },
        )
        assert response.status_code == 400, response.text
        saved = client.get("/api/v1/config/ai-search", headers=auth_headers)
        assert saved.json()["settings"]["sparse_expansion_enabled"] is False

    def test_audits_search_policy_changes(self, client, db_session, make_user):
        from tests.factories import bearer

        actor = make_user(superuser=True)
        response = client.patch(
            "/api/v1/search/settings",
            headers=bearer(actor),
            json={"enabled": True, "local_models_enabled": True},
        )
        assert response.status_code == 200, response.text
        record = db_session.exec(
            select(AuditLog).where(AuditLog.action == "ai_search_settings_changed")
        ).one()
        assert record.actor_id == actor.id
        assert record.resource_type == "ai_search"
        policy = json.loads(record.diff_json)
        assert policy["enabled"] is True
        assert policy["local_models_enabled"] is True
        assert policy["captions_enabled"] is False

    def test_patches_settings_without_resetting_other_opt_ins(
        self, client, auth_headers
    ):
        enabled = client.put(
            "/api/v1/config/ai-search",
            headers=auth_headers,
            json={"enabled": True, "local_models_enabled": True},
        )
        assert enabled.status_code == 200
        changed = client.patch(
            "/api/v1/search/settings",
            headers=auth_headers,
            json={"lexical_backend": "ranked_like"},
        )
        assert changed.status_code == 200, changed.text
        value = client.get("/api/v1/search/settings", headers=auth_headers).json()
        assert value["settings"]["enabled"] is True
        assert value["settings"]["local_models_enabled"] is True
        assert value["settings"]["lexical_backend"] == "ranked_like"

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("GET", "/settings", None),
            ("PATCH", "/settings", {"enabled": True}),
            ("GET", "/generations", None),
            ("GET", "/generations/1", None),
            ("POST", "/generations", {"endpoint_id": 1}),
            ("POST", "/generations/estimate", {"endpoint_id": 1}),
            *[
                ("POST", f"/generations/1/{action}", {"version_token": "a" * 32})
                for action in ("activate", "cancel", "retry")
            ],
        ],
    )
    def test_protects_canonical_administration_routes(
        self, client, user_headers, method, path, body
    ):
        response = client.request(
            method, f"/api/v1/search{path}", headers=user_headers(), json=body
        )
        assert response.status_code == 403, response.text

    def test_persists_retrieval_opt_in(self, client, auth_headers):
        response = client.put(
            "/api/v1/config/ai-search", json={"enabled": True}, headers=auth_headers
        )

        assert response.status_code == 200, response.text
        assert (
            client.get("/api/v1/config/ai-search", headers=auth_headers).json()[
                "settings"
            ]["enabled"]
            is True
        )

    @pytest.mark.parametrize(
        "field", ["captions_enabled", "nl_filters_enabled"], ids=["captions", "nl"]
    )
    def test_requires_a_capable_chat_endpoint(self, client, auth_headers, field):
        response = client.put(
            "/api/v1/config/ai-search", json={field: True}, headers=auth_headers
        )

        assert response.status_code == 400, response.text
        assert (
            client.get("/api/v1/config/ai-search", headers=auth_headers).json()[
                "settings"
            ][field]
            is False
        )

    def test_requires_admin_settings_changes(self, client, user_headers):
        response = client.put(
            "/api/v1/config/ai-search", json={"enabled": True}, headers=user_headers()
        )

        assert response.status_code == 403, response.text


class TestProposeGeneration:
    @pytest.mark.parametrize("suffix", ["", "/estimate"], ids=["proposal", "estimate"])
    def test_rejects_unknown_local_generation_models(
        self, client, auth_headers, db_session, tmp_path, monkeypatch, suffix
    ):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "embedding_cache_dir", tmp_path)
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        response = client.patch(
            "/api/v1/search/settings",
            headers=auth_headers,
            json={"enabled": True, "local_models_enabled": True},
        )
        assert response.status_code == 200, response.text

        response = client.post(
            "/api/v1/search/generations" + suffix,
            headers=auth_headers,
            json={"local_model_id": "f" * 64, "index_backend": "numpy"},
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "embedding_model_not_found"
        assert db_session.exec(select(IndexGeneration)).all() == []

    def test_estimates_a_proposed_local_generation(
        self, client, auth_headers, db_session, tmp_path, monkeypatch
    ):
        from app.core.config import _overlay
        from app.modules.inference import model_cache
        from tests.factories.embeddings import text_embedding_assets

        directory = text_embedding_assets(tmp_path / "cache" / "model")
        monkeypatch.setitem(_overlay, "embedding_cache_dir", directory.parent)
        monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
        model = model_cache.inspect(directory)

        response = client.post(
            "/api/v1/search/generations/estimate",
            headers=auth_headers,
            json={"local_model_id": model.id, "index_backend": "numpy"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["fits_budget"] is True
        assert response.json()["estimated_bytes"] >= 0
        assert response.json()["estimated_seconds"] is None
        assert db_session.exec(select(IndexGeneration)).all() == []

    def test_exposes_the_canonical_generation_lifecycle(
        self, client, auth_headers, make_inference_endpoint
    ):
        endpoint = make_inference_endpoint()
        assert (
            client.patch(
                "/api/v1/search/settings", headers=auth_headers, json={"enabled": True}
            ).status_code
            == 200
        )
        response = client.post(
            "/api/v1/search/generations",
            headers=auth_headers,
            json={"endpoint_id": endpoint.id, "index_backend": "numpy"},
        )
        assert response.status_code == 202, response.text
        proposal = response.json()
        path = f"/api/v1/search/generations/{proposal['id']}"
        assert client.get(path, headers=auth_headers).json() == proposal
        assert client.get(
            "/api/v1/search/generations", headers=auth_headers
        ).json() == [proposal]
        cancelled = client.post(
            path + "/cancel",
            headers=auth_headers,
            json={"version_token": proposal["version_token"]},
        )
        assert cancelled.status_code == 200, cancelled.text
        assert client.get(path, headers=auth_headers).json()["state"] == "cancelled"
        assert (
            client.get(
                "/api/v1/search/generations/9999", headers=auth_headers
            ).status_code
            == 404
        )

    def test_exposes_a_durable_build_job(
        self, client, auth_headers, make_inference_endpoint
    ):
        endpoint = make_inference_endpoint()
        enabled = client.put(
            "/api/v1/config/ai-search", headers=auth_headers, json={"enabled": True}
        )
        assert enabled.status_code == 200, enabled.text

        response = client.post(
            "/api/v1/config/ai-search/generations",
            headers=auth_headers,
            json={"endpoint_id": endpoint.id, "index_backend": "numpy"},
        )

        assert response.status_code == 202, response.text
        assert response.json()["state"] == "building"
        assert response.json()["job_id"]
        listed = client.get(
            "/api/v1/config/ai-search/generations", headers=auth_headers
        )
        assert listed.json() == [response.json()]

    def test_rejects_unavailable_recipes(
        self, client, auth_headers, make_inference_endpoint
    ):
        endpoint = make_inference_endpoint()
        enabled = client.put(
            "/api/v1/config/ai-search", headers=auth_headers, json={"enabled": True}
        )
        assert enabled.status_code == 200, enabled.text

        response = client.post(
            "/api/v1/config/ai-search/generations",
            headers=auth_headers,
            json={"endpoint_id": endpoint.id, "passage_recipe_version": 999},
        )

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == "request_validation_failed"
        assert response.json()["errors"][0]["loc"] == ["body", "passage_recipe_version"]
        assert (
            client.get(
                "/api/v1/config/ai-search/generations", headers=auth_headers
            ).json()
            == []
        )

    def test_requires_admin_generation_proposals(
        self, client, user_headers, db_session
    ):
        response = client.post(
            "/api/v1/config/ai-search/generations",
            headers=user_headers(),
            json={"endpoint_id": 1},
        )

        assert response.status_code == 403, response.text
        assert db_session.exec(select(IndexGeneration)).all() == []


class TestCancelGeneration:
    def test_cancels_the_selected_proposal(
        self, client, auth_headers, make_inference_endpoint
    ):
        endpoint = make_inference_endpoint()
        enabled = client.put(
            "/api/v1/config/ai-search", headers=auth_headers, json={"enabled": True}
        )
        assert enabled.status_code == 200, enabled.text
        proposal = client.post(
            "/api/v1/config/ai-search/generations",
            headers=auth_headers,
            json={"endpoint_id": endpoint.id, "index_backend": "numpy"},
        ).json()

        response = client.post(
            f"/api/v1/config/ai-search/generations/{proposal['id']}/cancel",
            headers=auth_headers,
            json={"version_token": proposal["version_token"]},
        )

        assert response.status_code == 200, response.text
        assert response.json()["state"] == "cancelled"

    @pytest.mark.parametrize(
        "action", ["activate", "cancel", "retry"], ids=["activate", "cancel", "retry"]
    )
    def test_requires_admin_generation_actions(self, client, user_headers, action):
        response = client.post(
            f"/api/v1/config/ai-search/generations/1/{action}",
            headers=user_headers(),
            json={"version_token": "a" * 32},
        )

        assert response.status_code == 403, response.text
