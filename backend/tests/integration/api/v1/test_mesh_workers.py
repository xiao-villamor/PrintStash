"""Administrators choose how many native mesh jobs run at once.

A stored count overlays ``max_render_jobs`` and resizes the ``derive.native``
lane without a restart; clearing it returns to the environment value or the
automatic default. A count the native memory pool cannot fit is refused.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import ConfigResolver, FrozenSettings, ProcessRole, _overlay
from app.core.host_cpus import NATIVE_JOB_FLOOR_BYTES
from app.db.models import LaneName, SystemConfig, User, WorkLaneOverride
from app.modules.administration import mesh_workers_config
from app.modules.identity.auth import create_access_token
from tests.factories import build_user

_URL = "/api/v1/config/mesh-workers"


@pytest.fixture
def mesh_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """A native pool that fits exactly four thumbnail floors."""
    previous = dict(_overlay)
    monkeypatch.setattr(
        mesh_workers_config,
        "native_pool_bytes",
        lambda _fraction: 4 * NATIVE_JOB_FLOOR_BYTES,
    )
    yield
    _overlay.clear()
    _overlay.update(previous)


def _put(client: TestClient, headers: dict[str, str], jobs: int):
    etag = client.get(_URL, headers=headers).headers["ETag"]
    return client.put(_URL, json={"jobs": jobs}, headers={**headers, "If-Match": etag})


@pytest.mark.usefixtures("mesh_pool")
class TestMeshWorkersConfig:
    def test_reports_the_automatic_count_without_a_stored_value(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        response = client.get(_URL, headers=auth_headers)
        body = response.json()
        assert response.status_code == 200
        assert response.headers["ETag"].startswith('"vault-config-e')
        assert body["source"] == "automatic"
        assert body["configured"] is None
        assert body["jobs"] == body["automatic"]
        assert body["maximum"] == 4
        assert body["restart_required"] is False

    def test_a_stored_count_resizes_admission_with_the_native_lane(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        work_engine,
    ) -> None:
        from app.core.config import settings

        response = _put(client, auth_headers, 2)
        body = response.json()
        assert response.status_code == 200
        assert (body["jobs"], body["configured"], body["source"]) == (
            2,
            2,
            "database",
        )
        assert settings.max_render_jobs == 2
        assert work_engine.concurrency[LaneName.DERIVE_NATIVE] == 2
        config = db_session.get(SystemConfig, 1)
        assert config is not None and config.max_render_jobs == 2

    def test_an_administrators_lane_override_still_wins(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        db_session: Session,
        work_engine,
    ) -> None:
        db_session.add(WorkLaneOverride(lane=LaneName.DERIVE_NATIVE, concurrency=1))
        db_session.commit()
        _put(client, auth_headers, 3)
        assert work_engine.concurrency[LaneName.DERIVE_NATIVE] == 1

    def test_reset_returns_to_the_automatic_count(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        work_engine,
    ) -> None:
        from app.core.config import settings

        automatic = client.get(_URL, headers=auth_headers).json()["automatic"]
        _put(client, auth_headers, 4 if automatic != 4 else 1)
        etag = client.get(_URL, headers=auth_headers).headers["ETag"]
        response = client.delete(_URL, headers={**auth_headers, "If-Match": etag})
        body = response.json()
        assert (body["source"], body["configured"]) == ("automatic", None)
        assert "max_render_jobs" not in _overlay
        assert settings.max_render_jobs == body["jobs"]
        assert work_engine.concurrency[LaneName.DERIVE_NATIVE] == body["jobs"]

    def test_refuses_more_jobs_than_the_pool_fits(
        self, client: TestClient, auth_headers: dict[str, str], db_session: Session
    ) -> None:
        response = _put(client, auth_headers, 5)
        assert response.status_code == 400
        assert response.json()["detail"] == "mesh_workers_exceed_memory"
        config = db_session.get(SystemConfig, 1)
        assert config is None or config.max_render_jobs is None

    @pytest.mark.parametrize("jobs", [0, -1])
    def test_refuses_a_count_below_one(
        self, client: TestClient, auth_headers: dict[str, str], jobs: int
    ) -> None:
        assert _put(client, auth_headers, jobs).status_code == 422

    def test_refuses_a_stale_editing_base(
        self, client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        stale = client.get(_URL, headers=auth_headers).headers["ETag"]
        _put(client, auth_headers, 2)
        response = client.put(
            _URL, json={"jobs": 3}, headers={**auth_headers, "If-Match": stale}
        )
        assert response.status_code == 412

    def test_reports_the_environment_count(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            mesh_workers_config,
            "settings",
            ConfigResolver(FrozenSettings(max_render_jobs=2)),
        )
        body = client.get(_URL, headers=auth_headers).json()
        assert (body["source"], body["jobs"], body["configured"]) == (
            "environment",
            2,
            None,
        )

    def test_a_split_deployment_applies_it_when_workers_restart(
        self,
        client: TestClient,
        auth_headers: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            mesh_workers_config,
            "settings",
            ConfigResolver(FrozenSettings(process_role=ProcessRole.API)),
        )
        assert client.get(_URL, headers=auth_headers).json()["restart_required"]

    def test_requires_authentication(self, client: TestClient) -> None:
        assert client.get(_URL).status_code == 401

    def test_is_for_administrators_only(
        self, client: TestClient, db_session: Session
    ) -> None:
        member: User = build_user(db_session, "mesh-member")
        assert member.id is not None
        token = create_access_token(member.id, member.username, scope="admin")
        response = client.get(_URL, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 403


class TestMeshWorkersOverlay:
    def test_startup_applies_the_stored_count(self, db_session: Session) -> None:
        from app.core.config import settings
        from app.modules.administration.runtime_config import apply_overlay

        previous = dict(_overlay)
        config = db_session.get(SystemConfig, 1) or SystemConfig(id=1)
        config.max_render_jobs = 2
        db_session.add(config)
        db_session.commit()
        try:
            apply_overlay(db_session)
            assert settings.max_render_jobs == 2
        finally:
            _overlay.clear()
            _overlay.update(previous)
