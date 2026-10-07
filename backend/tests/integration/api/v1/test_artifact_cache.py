"""Only administrators can change disposable cache policy."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from app.core.config import _overlay
from app.modules.storage.artifact_materializer import ArtifactMaterializer, CachePolicy
from app.modules.storage.materializer_runtime import bind_materializer, get_materializer


@pytest.fixture
def cache_settings_env(tmp_path):
    previous = dict(_overlay)
    original = get_materializer()
    cache = ArtifactMaterializer(tmp_path / "cache", lambda: CachePolicy())
    bind_materializer(cache)
    yield cache
    bind_materializer(original)
    _overlay.clear()
    _overlay.update(previous)


class TestArtifactCacheConfig:
    def test_persists_live_policy(self, client, auth_headers, cache_settings_env):
        policy = {
            **asdict(CachePolicy(enabled=True, max_bytes=500)),
            "root": str(cache_settings_env.root),
        }
        response = client.put(
            "/api/v1/config/artifact-cache", json=policy, headers=auth_headers
        )
        assert response.status_code == 200
        assert response.json()["policy"] == policy
        assert response.json()["source"] == "database"
        assert (
            client.get("/api/v1/config/artifact-cache", headers=auth_headers).json()[
                "policy"
            ]
            == policy
        )

    def test_reports_root_restart_requirement(
        self, client, auth_headers, cache_settings_env, tmp_path
    ):
        response = client.put(
            "/api/v1/config/artifact-cache",
            json={**asdict(CachePolicy()), "root": str(tmp_path / "replacement")},
            headers=auth_headers,
        )
        assert response.json()["restart_required"] is True
        assert response.json()["effective_root"] == str(cache_settings_env.root)
        assert not (tmp_path / "replacement").exists()

    def test_reset_restores_environment_defaults(
        self, client, auth_headers, cache_settings_env
    ):
        initial = client.get(
            "/api/v1/config/artifact-cache", headers=auth_headers
        ).json()["policy"]
        client.put(
            "/api/v1/config/artifact-cache",
            json={**initial, "max_bytes": 123},
            headers=auth_headers,
        )
        reset = client.delete("/api/v1/config/artifact-cache", headers=auth_headers)
        assert reset.json()["policy"] == initial
        assert reset.json()["source"] == "environment"

    def test_requires_authentication(self, client):
        assert client.get("/api/v1/config/artifact-cache").status_code == 401

    def test_validates_fill_limit(self, client, auth_headers, cache_settings_env):
        response = client.put(
            "/api/v1/config/artifact-cache",
            json={
                **asdict(CachePolicy()),
                "root": str(cache_settings_env.root),
                "max_fills": 0,
            },
            headers=auth_headers,
        )
        assert response.status_code == 422

    def test_clear_preserves_policy(self, client, auth_headers, cache_settings_env):
        initial = client.get(
            "/api/v1/config/artifact-cache", headers=auth_headers
        ).json()["policy"]
        cleared = client.post(
            "/api/v1/config/artifact-cache/clear", headers=auth_headers
        )
        assert cleared.status_code == 200
        assert cleared.json()["policy"] == initial
        assert cleared.json()["usage"]["bytes"] == 0

    def test_denies_nonadministrator_changes(self, client, user_headers):
        response = client.post(
            "/api/v1/config/artifact-cache/clear", headers=user_headers()
        )
        assert response.status_code == 403

    def test_reports_restart_after_unavailable_root_change(
        self, client, auth_headers, cache_settings_env, tmp_path
    ):
        from app.modules.storage.materializer_runtime import bind_materializer

        bind_materializer(None, configured_root=cache_settings_env.root)
        response = client.put(
            "/api/v1/config/artifact-cache",
            json={
                **asdict(CachePolicy(enabled=True)),
                "root": str(tmp_path / "replacement"),
            },
            headers=auth_headers,
        )
        assert response.json()["restart_required"] is True
        assert response.json()["effective_root"] == str(cache_settings_env.root)

    def test_rejects_authoritative_root_overlap(
        self, client, auth_headers, cache_settings_env
    ):
        from app.core.config import settings

        response = client.put(
            "/api/v1/config/artifact-cache",
            json={
                **asdict(CachePolicy()),
                "root": str(settings.data_dir),
            },
            headers=auth_headers,
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "cache_root_overlaps_managed_storage"

    def test_health_marks_only_disposable_cache_degraded(
        self, client, auth_headers, cache_settings_env, monkeypatch
    ):
        monkeypatch.setitem(_overlay, "artifact_cache_enabled", True)
        cache_settings_env.policy = lambda: CachePolicy(enabled=True)
        (cache_settings_env.root / "index.sqlite3").write_bytes(b"invalid index")
        response = client.get("/api/v1/health/details", headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["components"]["artifact_cache"] == {
            "ok": False,
            "state": "corrupt_index",
        }
        assert response.json()["status"] == "degraded"

    def test_shrinking_policy_reclaims_idle_entries(
        self, client, auth_headers, cache_settings_env
    ):
        import hashlib
        import time

        from app.modules.administration.artifact_cache_config import live_policy
        from app.modules.storage.artifact_materializer import Representation

        cache = cache_settings_env
        cache.policy = live_policy
        policy = {
            **asdict(CachePolicy(enabled=True, headroom_bytes=0)),
            "root": str(cache.root),
        }
        client.put("/api/v1/config/artifact-cache", headers=auth_headers, json=policy)
        representation = Representation(
            "artifact", 1, hashlib.sha256(b"content").hexdigest(), 7
        )
        with cache.materialize(representation, lambda: iter([b"content"])):
            pass
        response = client.put(
            "/api/v1/config/artifact-cache",
            headers=auth_headers,
            json={**policy, "max_bytes": 0},
        )
        assert response.status_code == 200
        deadline = time.monotonic() + 5
        while cache.status()["bytes"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert cache.status()["bytes"] == 0


class TestCachePolicyEditing:
    @staticmethod
    def _conditional(response, auth_headers):
        row = response.json()
        return {
            **auth_headers,
            "X-PrintStash-Edit-Contract": "conditional-v1",
            "If-Match": f'"vault-config-e{row["edit_epoch"]}-v{row["edit_version"]}"',
        }

    def test_rejects_obsolete_cache_policy_saves(
        self, client, auth_headers, cache_settings_env
    ):
        first = client.get("/api/v1/config/artifact-cache", headers=auth_headers)
        headers = self._conditional(first, auth_headers)
        winner = client.put(
            "/api/v1/config/artifact-cache",
            headers=headers,
            json={**first.json()["policy"], "max_bytes": 123},
        )
        assert winner.status_code == 200
        assert winner.json()["edit_version"] == first.json()["edit_version"] + 1
        rejected = client.put(
            "/api/v1/config/artifact-cache",
            headers=headers,
            json={**first.json()["policy"], "max_bytes": 456},
        )
        assert rejected.status_code == 412
        assert (
            client.get("/api/v1/config/artifact-cache", headers=auth_headers).json()[
                "policy"
            ]["max_bytes"]
            == 123
        )

    def test_rejects_obsolete_cache_policy_resets(
        self, client, auth_headers, cache_settings_env
    ):
        first = client.get("/api/v1/config/artifact-cache", headers=auth_headers)
        headers = self._conditional(first, auth_headers)
        client.put(
            "/api/v1/config/artifact-cache",
            headers=headers,
            json={**first.json()["policy"], "max_bytes": 123},
        )
        rejected = client.delete("/api/v1/config/artifact-cache", headers=headers)
        assert rejected.status_code == 412
        assert (
            client.get("/api/v1/config/artifact-cache", headers=auth_headers).json()[
                "source"
            ]
            == "database"
        )

    def test_advances_cache_edit_identity_for_legacy_writes(
        self, client, auth_headers, cache_settings_env
    ):
        first = client.get("/api/v1/config/artifact-cache", headers=auth_headers)
        headers = self._conditional(first, auth_headers)
        legacy = client.put(
            "/api/v1/config/artifact-cache",
            headers=auth_headers,
            json={**first.json()["policy"], "max_bytes": 123},
        )
        assert legacy.status_code == 200
        assert legacy.json()["edit_version"] > first.json()["edit_version"]
        assert (
            client.delete("/api/v1/config/artifact-cache", headers=headers).status_code
            == 412
        )

    def test_requires_a_base_for_opted_in_cache_policy_writes(
        self, client, auth_headers, cache_settings_env
    ):
        first = client.get("/api/v1/config/artifact-cache", headers=auth_headers)
        response = client.put(
            "/api/v1/config/artifact-cache",
            json=first.json()["policy"],
            headers={**auth_headers, "X-PrintStash-Edit-Contract": "conditional-v1"},
        )
        assert response.status_code == 428
        assert (
            client.get("/api/v1/config/artifact-cache", headers=auth_headers).json()
            == first.json()
        )
