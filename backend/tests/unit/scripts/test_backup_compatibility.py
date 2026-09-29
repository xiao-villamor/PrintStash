"""Historical recovery fixtures fail closed before any container is launched.

A corrupt/replaced archive, wrong release, or missing corpus must never become
an apparently successful compatibility check or overwrite preserved evidence.
"""

from __future__ import annotations

import importlib.util
import json
from types import ModuleType

import pytest

from tests.paths import REPO_ROOT


@pytest.fixture(scope="module")
def compatibility() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "backup_compatibility", REPO_ROOT / "scripts" / "backup_compatibility.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAssetNames:
    def test_names_assets_for_the_exact_release(self, compatibility):
        assert compatibility.asset_names("0.14.0") == (
            "demo-backup-0.14.0.tar.gz",
            "demo-backup-0.14.0.json",
        )

    @pytest.mark.parametrize(
        "version",
        ["../0.14.0", "v0.14.0", "0.14", "0.14.0-rc1"],
        ids=["path", "tag", "incomplete", "prerelease"],
    )
    def test_rejects_non_release_versions(self, compatibility, version):
        with pytest.raises(ValueError, match="version must be X.Y.Z"):
            compatibility.asset_names(version)


class TestCreate:
    @pytest.mark.parametrize(
        "filename",
        ["demo-backup-0.14.0.tar.gz", "demo-backup-0.14.0.json"],
        ids=["archive", "oracle"],
    )
    def test_refuses_to_overwrite_a_historical_fixture(
        self, compatibility, tmp_path, filename
    ):
        existing = tmp_path / filename
        existing.write_bytes(b"immutable evidence")

        with pytest.raises(FileExistsError, match="fixtures are immutable"):
            compatibility.create("unused", "0.14.0", tmp_path, tmp_path)

        assert existing.read_bytes() == b"immutable evidence"

    def test_rejects_an_empty_testdata_corpus(self, compatibility, tmp_path):
        with pytest.raises(ValueError, match="no supported Artifacts"):
            compatibility.create("unused", "0.14.0", tmp_path, tmp_path)


class TestVerify:
    def test_rejects_a_fixture_without_the_documented_demo_key(
        self, compatibility, tmp_path
    ):
        archive = b"historical archive bytes"
        oracle = {
            "format": 1,
            "version": "0.14.0",
            "archive_sha256": compatibility.digest(archive),
            "demo_secrets_key": "different-test-key",
        }
        (tmp_path / "demo-backup-0.14.0.tar.gz").write_bytes(archive)
        (tmp_path / "demo-backup-0.14.0.json").write_text(json.dumps(oracle))

        with pytest.raises(ValueError, match="documented public demo key"):
            compatibility.verify("unused", "0.14.0", tmp_path, "0.14.1")

    @pytest.mark.parametrize(
        "change",
        [{"format": 99}, {"version": "0.13.0"}, {"archive_sha256": "0" * 64}],
        ids=["unknown-format", "wrong-release", "changed-bytes"],
    )
    def test_rejects_mismatched_fixture_evidence(self, compatibility, tmp_path, change):
        archive = b"historical archive bytes"
        oracle = {
            "format": 1,
            "version": "0.14.0",
            "archive_sha256": compatibility.digest(archive),
            **change,
        }
        (tmp_path / "demo-backup-0.14.0.tar.gz").write_bytes(archive)
        (tmp_path / "demo-backup-0.14.0.json").write_text(json.dumps(oracle))

        with pytest.raises(ValueError, match="identity/checksum mismatch"):
            compatibility.verify("unused", "0.14.0", tmp_path, "0.14.1")

    def test_fails_when_the_historical_archive_is_missing(
        self, compatibility, tmp_path
    ):
        (tmp_path / "demo-backup-0.14.0.json").write_text("{}")

        with pytest.raises(FileNotFoundError, match="demo-backup-0.14.0.tar.gz"):
            compatibility.verify("unused", "0.14.0", tmp_path, "0.14.1")
