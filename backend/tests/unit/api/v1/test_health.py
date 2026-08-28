"""The health probes' pure logic and their reaction to a broken dependency.

``/health/details`` is what a self-hoster's Docker healthcheck and the release
verification in ``docs/release-validation.md`` read to decide the service is up, so a
probe that raises instead of reporting takes the whole endpoint down with it. Every
probe here must turn a failure into ``{"ok": False, "error": <class name>}`` and keep
whatever it can still report — the provider probe still lists provider capabilities, the
backup probe still names its path. When one of these goes red, an operator's health
endpoint has started returning a 500 instead of a diagnosis.

The probes that read real rows live in ``integration/api/v1/test_health.py``; this file
only holds the branches a real dependency cannot produce on demand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import app.api.v1.health as health_mod
from app.core.config import _overlay
from app.db.models import PrinterProvider

THUMBNAIL_MODULES = ("numpy", "PIL", "trimesh")


class _RaisingSessionFactory:
    """A session factory that fails the way an unreachable database does."""

    def __init__(self, message: str = "db unreachable") -> None:
        self._message = message

    def session(self):
        raise RuntimeError(self._message)


@pytest.fixture
def broken_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point every probe's session factory at a database that refuses to connect."""
    monkeypatch.setattr(
        health_mod, "get_session_factory", lambda: _RaisingSessionFactory()
    )


def _installed(*present: str):
    """A ``find_spec`` that reports exactly *present* as importable."""
    return lambda module: object() if module in present else None


class TestRuntimeCapabilities:
    def test_reports_the_image_variant_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PRINTSTASH_IMAGE_VARIANT", "full")

        assert health_mod._runtime_capabilities()["image_variant"] == "full"

    def test_defaults_the_image_variant_to_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("PRINTSTASH_IMAGE_VARIANT", raising=False)

        assert health_mod._runtime_capabilities()["image_variant"] == "source"

    @pytest.mark.parametrize(
        ("capability", "module"),
        [
            pytest.param("browser", "patchright", id="browser-capture"),
            pytest.param("step", "cascadio", id="step-tessellation"),
        ],
    )
    @pytest.mark.parametrize("present", [True, False], ids=["installed", "missing"])
    def test_reports_an_optional_capability(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capability: str,
        module: str,
        present: bool,
    ) -> None:
        monkeypatch.setattr(
            health_mod, "find_spec", _installed(*((module,) if present else ()))
        )

        assert health_mod._runtime_capabilities()[capability] is present

    def test_reports_thumbnails_when_every_dependency_is_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(health_mod, "find_spec", _installed(*THUMBNAIL_MODULES))

        assert health_mod._runtime_capabilities()["thumbnails"] is True

    @pytest.mark.parametrize("missing", THUMBNAIL_MODULES, ids=str)
    def test_requires_every_thumbnail_dependency(
        self, monkeypatch: pytest.MonkeyPatch, missing: str
    ) -> None:
        present = tuple(m for m in THUMBNAIL_MODULES if m != missing)
        monkeypatch.setattr(health_mod, "find_spec", _installed(*present))

        assert health_mod._runtime_capabilities()["thumbnails"] is False


class TestDatabaseProbe:
    def test_reports_the_exception_class_when_the_session_fails(
        self, broken_database: None
    ) -> None:
        out = health_mod._database_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"


class TestBackupProbe:
    def test_reports_an_empty_backup_directory(self, tmp_path: Path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        _overlay["backup_dir"] = backups

        out = health_mod._backup_probe()

        assert out["ok"] is True
        assert out["local_count"] == 0
        assert out["latest"] is None

    def test_names_the_most_recent_archive(self, tmp_path: Path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "printstash-backup-20260101-000000.tar.gz").write_bytes(b"a")
        (backups / "printstash-backup-20260202-000000.tar.gz").write_bytes(b"b")
        _overlay["backup_dir"] = backups

        out = health_mod._backup_probe()

        assert out["local_count"] == 2
        assert out["latest"] == "printstash-backup-20260202-000000.tar.gz"

    def test_counts_archives_under_the_legacy_name(self, tmp_path: Path) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        (backups / "nexus3d-backup-20250101-000000.tar.gz").write_bytes(b"a")
        _overlay["backup_dir"] = backups

        out = health_mod._backup_probe()

        assert out["local_count"] == 1, "a pre-rename archive is still a backup"

    def test_is_not_ok_when_the_directory_is_missing(self, tmp_path: Path) -> None:
        _overlay["backup_dir"] = tmp_path / "does-not-exist"

        out = health_mod._backup_probe()

        assert out["ok"] is False

    @pytest.mark.parametrize(
        ("bucket", "expected"),
        [
            pytest.param("printstash-backups", True, id="bucket-configured"),
            pytest.param("", False, id="no-bucket"),
        ],
    )
    def test_reports_whether_an_s3_destination_is_configured(
        self, tmp_path: Path, bucket: str, expected: bool
    ) -> None:
        backups = tmp_path / "backups"
        backups.mkdir()
        _overlay["backup_dir"] = backups
        _overlay["backup_s3_bucket"] = bucket

        assert health_mod._backup_probe()["s3_configured"] is expected

    def test_reports_the_exception_class_when_the_directory_cannot_be_read(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        # A real Path.glob() swallows PermissionError, so the OSError branch is
        # reachable only through a Path bound in the health module's namespace. The
        # pathlib.Path every other module holds is untouched.
        _overlay["backup_dir"] = tmp_path / "backups"

        class _UnreadableDir:
            def __init__(self, *_args: object, **_kwargs: object) -> None: ...

            def glob(self, *_args: object, **_kwargs: object):
                raise OSError("disk error")

            def exists(self) -> bool:
                return True

            def is_dir(self) -> bool:
                return True

            def __str__(self) -> str:
                return str(tmp_path / "backups")

        monkeypatch.setattr(health_mod, "Path", _UnreadableDir)

        out = health_mod._backup_probe()

        assert out["ok"] is False
        assert out["error"] == "OSError"
        assert out["path"] == str(tmp_path / "backups")


class TestStorageProbe:
    def test_delegates_to_the_storage_backends_probe(self) -> None:
        from app.services.storage_backend import get_backend

        out = health_mod._storage_probe()

        backend = get_backend()
        for key, value in backend.health_probe().items():
            assert out[key] == value
        assert out["provider"] == "local"
        assert out["tier"] == "verified"
        assert out["warnings"] == []

    def test_reports_the_exception_class_when_the_backend_is_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def unavailable():
            raise RuntimeError("backend unavailable")

        monkeypatch.setattr(health_mod, "get_backend", unavailable)

        out = health_mod._storage_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"
        assert out["backend"], "the configured backend is still named"


class TestProviderProbe:
    def test_summarises_every_provider_in_the_registry(self) -> None:
        # Derived from the enum, so a new provider is covered the day it is added.
        summarised = {p["provider"] for p in health_mod._provider_probe()["providers"]}

        assert summarised == {p.value for p in PrinterProvider}

    def test_still_summarises_providers_when_the_database_fails(
        self, broken_database: None
    ) -> None:
        out = health_mod._provider_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"
        assert out["providers"], "capability reporting does not depend on the database"


class TestJobsProbe:
    def test_reports_the_registrys_job_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.services.jobs as jobs_mod

        counts = {"pending": 1, "running": 2, "total": 3}
        monkeypatch.setattr(jobs_mod.registry, "snapshot_counts", lambda: counts)

        assert health_mod._jobs_probe() == {"ok": True, "counts": counts}

    def test_reports_the_exception_class_when_the_registry_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.services.jobs as jobs_mod

        def broken():
            raise RuntimeError("registry broken")

        monkeypatch.setattr(jobs_mod.registry, "snapshot_counts", broken)

        out = health_mod._jobs_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"


class TestFleetSchedulerProbe:
    def test_reports_the_exception_class_when_the_database_fails(
        self, broken_database: None
    ) -> None:
        out = health_mod._fleet_scheduler_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"


class TestExternalLibrariesProbe:
    def test_reports_the_exception_class_when_the_database_fails(
        self, broken_database: None
    ) -> None:
        out = health_mod._external_libraries_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"


class TestSpoolmanProbe:
    def test_reports_the_exception_class_when_the_database_fails(
        self, broken_database: None
    ) -> None:
        out = health_mod._spoolman_probe()

        assert out["ok"] is False
        assert out["error"] == "RuntimeError"
