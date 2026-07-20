"""Branch coverage for backup.verify_backup's archive-corruption checks —
unsafe/duplicate members, symlinks, bad manifests, missing/mismatched files,
and version incompatibility — beyond the happy path in test_backup_restore.py."""

from __future__ import annotations

import gzip
import io
import json
import tarfile
from pathlib import Path

import pytest

from app.services import backup
from tests.test_backup_restore import (  # noqa: F811 — fixture reuse, not a real redefinition
    BackupEnv,
    _seed_model_with_blob,
    backup_env,
)

__all__ = ["backup_env"]


def _verify_direct(archive: Path, monkeypatch: pytest.MonkeyPatch) -> "backup.BackupVerification":
    """Bypass discovery (_list_local_backups re-reads the manifest to find the
    backup by id) and validate *archive* directly — some corruptions here also
    break discovery, which isn't what these tests are checking."""
    monkeypatch.setattr(backup, "get_backup_archive_path", lambda _id: archive)
    return backup.verify_backup(_id_from(archive))


def _extract(archive: Path) -> tuple[dict[str, bytes], dict]:
    contents: dict[str, bytes] = {}
    manifest: dict = {}
    with gzip.open(archive, "rb") as gz, tarfile.open(fileobj=gz, mode="r:") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            data = tar.extractfile(member).read()
            contents[member.name] = data
            if member.name == "manifest.json":
                manifest = json.loads(data.decode("utf-8"))
    return contents, manifest


def _write(archive: Path, contents: dict[str, bytes], *, extra_symlink: str | None = None) -> None:
    with gzip.open(archive, "wb") as gz, tarfile.open(fileobj=gz, mode="w:") as tar:
        for name, data in contents.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        if extra_symlink:
            info = tarfile.TarInfo(name=extra_symlink)
            info.type = tarfile.SYMTYPE
            info.linkname = "manifest.json"
            tar.addfile(info)


def _id_from(archive: Path) -> str:
    return archive.name.removesuffix(".tar.gz").rsplit("-", 1)[-1]


def _fresh_archive(env: BackupEnv) -> tuple[Path, dict[str, bytes], dict]:
    _seed_model_with_blob(env, name="Verified", content=b"solid verified\n")
    meta = backup.create_backup()
    archive = Path(meta.path)
    contents, manifest = _extract(archive)
    return archive, contents, manifest


def test_verify_backup_flags_unsafe_member_name(backup_env: BackupEnv) -> None:
    archive, contents, _ = _fresh_archive(backup_env)
    contents["../escape.txt"] = b"evil"
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)


def test_verify_backup_flags_symlink_member(backup_env: BackupEnv) -> None:
    archive, contents, _ = _fresh_archive(backup_env)
    _write(archive, contents, extra_symlink="sneaky-link")

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)


def test_verify_backup_flags_missing_manifest(backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    archive, contents, _ = _fresh_archive(backup_env)
    del contents["manifest.json"]
    _write(archive, contents)

    result = _verify_direct(archive, monkeypatch)

    assert result.valid is False
    assert any(
        f["code"] == "backup_manifest_invalid" and f["member"] == "manifest.json"
        for f in result.findings
    )
    assert result.app_compatible is False


def test_verify_backup_flags_corrupt_manifest_json(backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    archive, contents, _ = _fresh_archive(backup_env)
    contents["manifest.json"] = b"{not valid json"
    _write(archive, contents)

    result = _verify_direct(archive, monkeypatch)

    assert result.valid is False
    assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)


def test_verify_backup_flags_non_dict_manifest(backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    archive, contents, _ = _fresh_archive(backup_env)
    contents["manifest.json"] = json.dumps(["not", "a", "dict"]).encode("utf-8")
    _write(archive, contents)

    result = _verify_direct(archive, monkeypatch)

    assert result.valid is False
    assert any(f["code"] == "backup_manifest_invalid" for f in result.findings)


def test_verify_backup_flags_missing_db_file(backup_env: BackupEnv) -> None:
    archive, contents, _ = _fresh_archive(backup_env)
    del contents["db.sqlite3"]
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(
        f["code"] == "backup_member_missing" and f["member"] == "db.sqlite3"
        for f in result.findings
    )


def test_verify_backup_flags_files_entry_not_a_list(backup_env: BackupEnv) -> None:
    archive, contents, manifest = _fresh_archive(backup_env)
    manifest["files"] = "not-a-list"
    contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(
        f["code"] == "backup_manifest_invalid" and f["member"] == "files"
        for f in result.findings
    )


def test_verify_backup_flags_malformed_file_entry(backup_env: BackupEnv) -> None:
    archive, contents, manifest = _fresh_archive(backup_env)
    manifest["files"] = [{"no_arc_key": True}]
    contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(
        f["code"] == "backup_manifest_invalid" and f["member"] == "files"
        for f in result.findings
    )


def test_verify_backup_flags_file_entry_missing_from_archive(backup_env: BackupEnv) -> None:
    archive, contents, manifest = _fresh_archive(backup_env)
    manifest["files"].append({"arc": "files/ghost.stl", "size": 5})
    contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(
        f["code"] == "backup_member_missing" and f["member"] == "files/ghost.stl"
        for f in result.findings
    )


def test_verify_backup_flags_file_size_mismatch(backup_env: BackupEnv) -> None:
    archive, contents, manifest = _fresh_archive(backup_env)
    entry = manifest["files"][0]
    entry["size"] = entry["size"] + 999
    contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.valid is False
    assert any(f["code"] == "backup_member_size_mismatch" for f in result.findings)


def test_verify_backup_flags_incompatible_manifest_version(backup_env: BackupEnv) -> None:
    archive, contents, manifest = _fresh_archive(backup_env)
    manifest["version"] = "999"
    contents["manifest.json"] = json.dumps(manifest).encode("utf-8")
    _write(archive, contents)

    result = backup.verify_backup(_id_from(archive))

    assert result.app_compatible is False
    assert any(
        f["code"] == "backup_manifest_invalid" and f["member"] == "version"
        for f in result.findings
    )


def test_verify_backup_flags_unreadable_archive(backup_env: BackupEnv, monkeypatch: pytest.MonkeyPatch) -> None:
    archive, _contents, _manifest = _fresh_archive(backup_env)
    archive.write_bytes(b"not a gzip file at all")

    result = _verify_direct(archive, monkeypatch)

    assert result.valid is False
    assert any(
        f["code"] == "backup_manifest_invalid" and f["member"] == "archive"
        for f in result.findings
    )
