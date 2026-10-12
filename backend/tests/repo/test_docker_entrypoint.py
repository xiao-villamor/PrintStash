"""The container entrypoint validates and applies the requested file identity.

Bind-mounted data must remain writable across restarts, while migrations and an
operator-supplied command must run only after the process has dropped privileges.
These tests execute the real shell flow with tiny command shims standing in for
the container-only ``id``, ``gosu``, and Python runtime.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.core.config import DATA_ROOT_LAYOUT
from tests.paths import REPO_ROOT

ENTRYPOINT = REPO_ROOT / "backend" / "docker-entrypoint.sh"
MANAGED_DIRECTORIES = sorted(
    {Path(path).parts[0] for path in DATA_ROOT_LAYOUT.values()}
)
# A developer shell may export these; each test chooses its own layout.
_PATH_OVERRIDES = frozenset(
    {
        "VAULT_DATA_DIR",
        "VAULT_THUMB_DIR",
        "VAULT_STAGING_DIR",
        "VAULT_BACKUP_DIR",
        "VAULT_ARTIFACT_CACHE_ROOT",
        "VAULT_EMBEDDING_CACHE_DIR",
    }
)


def _write_executable(path: Path, body: str) -> Path:
    path.write_text(body)
    path.chmod(0o755)
    return path


def _entrypoint_harness(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """Return a script variant, command log, fake PATH, and fake server."""

    command_log = tmp_path / "commands.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    _write_executable(
        fake_bin / "id",
        """#!/bin/sh
if [ "$1" = "-u" ]; then
  echo "${FAKE_UID:-0}"
else
  echo "${FAKE_GID:-0}"
fi
""",
    )
    _write_executable(
        fake_bin / "gosu",
        """#!/bin/sh
spec=$1
shift
FAKE_UID=${spec%:*} FAKE_GID=${spec#*:} exec "$@"
""",
    )
    _write_executable(
        fake_bin / "chown",
        f"""#!/bin/sh
printf 'chown:%s\\n' "$*" >> {command_log}
for entry in "$@"; do
  if [ -n "${{FAKE_READ_ONLY_ROOT:-}}" ] && [ "$entry" = "$FAKE_READ_ONLY_ROOT" ]; then
    printf 'chown: %s: Read-only file system\\n' "$entry" >&2
    exit 1
  fi
done
if [ "$1" = "-R" ]; then
  shift
  shift
  for managed_root in "$@"; do
    if [ -L "$managed_root/managed-descendant" ]; then
      printf 'dereferenced by unsafe chown\\n' > "$managed_root/managed-descendant"
    fi
  done
fi
""",
    )

    fake_python = _write_executable(
        tmp_path / "python",
        f"""#!/bin/sh
printf 'migration:%s:%s:%s\\n' "$FAKE_UID" "$FAKE_GID" "$*" >> {command_log}
""",
    )
    fake_server = _write_executable(
        tmp_path / "server",
        f"""#!/bin/sh
printf 'server:%s:%s:%s\\n' "$FAKE_UID" "$FAKE_GID" "$*" >> {command_log}
""",
    )

    source = ENTRYPOINT.read_text()
    source = source.replace("/app/.venv/bin/python", str(fake_python))
    script = _write_executable(tmp_path / "entrypoint.sh", source)
    return script, command_log, fake_bin, fake_server


def _run_entrypoint(
    script: Path,
    fake_bin: Path,
    fake_server: Path,
    *,
    tmp_path: Path,
    puid: str | None = None,
    pgid: str | None = None,
    role: str | None = None,
    overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        key: value for key, value in os.environ.items() if key not in _PATH_OVERRIDES
    }
    env.pop("VAULT_PROCESS_ROLE", None)
    env.pop("FAKE_READ_ONLY_ROOT", None)
    if role is not None:
        env["VAULT_PROCESS_ROLE"] = role
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["VAULT_DATA_ROOT"] = str(tmp_path / "data")
    env.update(overrides or {})
    if puid is None:
        env.pop("PUID", None)
    else:
        env["PUID"] = puid
    if pgid is None:
        env.pop("PGID", None)
    else:
        env["PGID"] = pgid
    return subprocess.run(
        [str(script), str(fake_server), "operator-arg"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _repaired_paths(log: Path, identity: str) -> list[str]:
    """Every path handed to chown for *identity*, one entry per hand-off."""
    prefix = f"chown:-h {identity} "
    return [
        path
        for line in log.read_text().splitlines()
        if line.startswith(prefix)
        for path in line.removeprefix(prefix).split(" ")
    ]


class TestDockerEntrypointIdentity:
    def test_runs_the_migration_as_the_unprivileged_default(
        self, tmp_path: Path
    ) -> None:
        """Migration is the first thing to touch the vault, so it sets the owner.

        Running it as root writes a database the dropped-to user cannot then open,
        and the container dies on its second start rather than its first.
        """
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(script, fake_bin, server, tmp_path=tmp_path)

        assert result.returncode == 0, result.stderr
        assert any(
            line.startswith("migration:10001:10001:")
            for line in log.read_text().splitlines()
        )

    def test_starts_the_server_as_the_unprivileged_default(
        self, tmp_path: Path
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(script, fake_bin, server, tmp_path=tmp_path)

        assert result.returncode == 0, result.stderr
        assert any(
            line.startswith("server:10001:10001:")
            for line in log.read_text().splitlines()
        )

    @pytest.mark.parametrize("directory", MANAGED_DIRECTORIES)
    def test_creates_the_layout_under_the_data_root(
        self, tmp_path: Path, directory: str
    ) -> None:
        script, _log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(script, fake_bin, server, tmp_path=tmp_path)

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "data" / directory).is_dir()

    @pytest.mark.parametrize(
        "directory",
        MANAGED_DIRECTORIES,
    )
    def test_re_owns_app_owned_directories_under_the_data_root(
        self, tmp_path: Path, directory: str
    ) -> None:
        """Every app-owned directory must remain writable after an ID change."""
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        (tmp_path / "data" / "ai-models").mkdir(parents=True)

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid="001234",
            pgid="002345",
        )

        assert result.returncode == 0, result.stderr
        repaired = _repaired_paths(log, "1234:2345")
        assert str(tmp_path / "data" / directory) in repaired

    @pytest.mark.parametrize(
        "relative_path", ["library", "sources/library with spaces"]
    )
    def test_starts_with_an_external_read_only_directory(
        self, tmp_path: Path, relative_path: str
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        source = tmp_path / "data" / relative_path
        source.mkdir(parents=True)

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            overrides={"FAKE_READ_ONLY_ROOT": str(source)},
        )

        assert result.returncode == 0, result.stderr
        assert _steps(log) == ["migration", "server"]

    def test_does_not_repair_external_directory_contents(self, tmp_path: Path) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        source_file = tmp_path / "data" / "library" / "part.stl"
        source_file.parent.mkdir(parents=True)
        source_file.write_bytes(b"solid")

        result = _run_entrypoint(script, fake_bin, server, tmp_path=tmp_path)

        assert result.returncode == 0, result.stderr
        assert str(source_file) not in _repaired_paths(log, "10001:10001")

    @pytest.mark.parametrize(
        ("variable", "directory"),
        [
            pytest.param(
                "VAULT_ARTIFACT_CACHE_ROOT", "artifact-cache", id="artifact-cache"
            ),
            pytest.param("VAULT_EMBEDDING_CACHE_DIR", "ai-models", id="ai-models"),
        ],
    )
    def test_creates_an_overridden_cache_directory(
        self, tmp_path: Path, variable: str, directory: str
    ) -> None:
        script, _log, fake_bin, server = _entrypoint_harness(tmp_path)
        cache = tmp_path / "ssd" / directory

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            overrides={variable: str(cache)},
        )

        assert result.returncode == 0, result.stderr
        assert cache.is_dir()

    @pytest.mark.parametrize(
        ("variable", "directory"),
        [
            pytest.param(
                "VAULT_ARTIFACT_CACHE_ROOT", "artifact-cache", id="artifact-cache"
            ),
            pytest.param("VAULT_EMBEDDING_CACHE_DIR", "ai-models", id="ai-models"),
        ],
    )
    def test_re_owns_an_overridden_cache_directory(
        self, tmp_path: Path, variable: str, directory: str
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        cache = tmp_path / "ssd" / directory
        cache.mkdir(parents=True)
        cached = cache / "cached.bin"
        cached.write_bytes(b"cache")

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            overrides={variable: str(cache)},
        )

        assert result.returncode == 0, result.stderr
        assert str(cached) in _repaired_paths(log, "10001:10001")

    @pytest.mark.parametrize("directory", [".", *MANAGED_DIRECTORIES])
    def test_managed_ownership_failure_stops_before_migration(
        self, tmp_path: Path, directory: str
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        managed = tmp_path / "data" / directory

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            overrides={"FAKE_READ_ONLY_ROOT": str(managed)},
        )

        assert result.returncode != 0
        assert "Read-only file system" in result.stderr
        assert _steps(log) == []

    def test_repairs_each_entry_once(self, tmp_path: Path) -> None:
        # The managed children sit on the root's own mount here, so a root walk
        # that did not prune them would hand every library file to chown twice.
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        artifact = tmp_path / "data" / "files" / "model" / "part.stl"
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"solid")

        result = _run_entrypoint(
            script, fake_bin, server, tmp_path=tmp_path, puid="1234", pgid="2345"
        )

        assert result.returncode == 0, result.stderr
        assert _repaired_paths(log, "1234:2345").count(str(artifact)) == 1

    def test_places_an_overridden_directory_outside_the_root(
        self, tmp_path: Path
    ) -> None:
        script, _log, fake_bin, server = _entrypoint_harness(tmp_path)
        backups = tmp_path / "hdd" / "backups"

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            overrides={"VAULT_BACKUP_DIR": str(backups)},
        )

        assert result.returncode == 0, result.stderr
        assert backups.is_dir()

    def test_re_owns_an_overridden_directory_outside_the_root(
        self, tmp_path: Path
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        backups = tmp_path / "hdd" / "backups"

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid="1234",
            pgid="2345",
            overrides={"VAULT_BACKUP_DIR": str(backups)},
        )

        assert result.returncode == 0, result.stderr
        assert str(backups) in _repaired_paths(log, "1234:2345")

    @pytest.mark.parametrize(
        ("variable", "directory"),
        [
            pytest.param("VAULT_STAGING_DIR", "staging", id="staging"),
            pytest.param(
                "VAULT_ARTIFACT_CACHE_ROOT", "artifact-cache", id="artifact-cache"
            ),
            pytest.param("VAULT_EMBEDDING_CACHE_DIR", "ai-models", id="ai-models"),
        ],
    )
    def test_treats_an_empty_override_as_the_default(
        self, tmp_path: Path, variable: str, directory: str
    ) -> None:
        # An unset Compose variable renders as an empty string, which must mean
        # the layout path and never the working directory.
        script, _log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            overrides={variable: ""},
        )

        assert result.returncode == 0, result.stderr
        assert (tmp_path / "data" / directory).is_dir()

    def test_preserves_metadata_for_entries_already_owned_by_runtime_identity(
        self, tmp_path: Path
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        archive = tmp_path / "data" / "backups" / "owned-backup.tar.gz"
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b"owned")

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid=str(os.getuid()),
            pgid=str(os.getgid()),
        )

        assert result.returncode == 0, result.stderr
        assert not any(
            line.startswith("chown:") for line in log.read_text().splitlines()
        )

    def test_passes_the_operator_command_through_unchanged(
        self, tmp_path: Path
    ) -> None:
        # The re-exec goes through `gosu "-e" ""`, so a lost argument would
        # silently start the default server instead of what the operator asked for.
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid="001234",
            pgid="002345",
        )

        assert result.returncode == 0, result.stderr
        assert "server:1234:2345:operator-arg" in log.read_text().splitlines()

    def test_identity_change_repairs_ownership_before_drop(
        self, tmp_path: Path
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        first = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid="1111",
            pgid="2222",
        )
        second = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid="3333",
            pgid="4444",
        )

        assert first.returncode == 0, first.stderr
        assert second.returncode == 0, second.stderr
        ownership_repairs = [
            line
            for line in log.read_text().splitlines()
            if line.startswith("chown:-h ")
        ]
        assert any("1111:2222" in line for line in ownership_repairs)
        assert any("3333:4444" in line for line in ownership_repairs)

    def test_managed_descendant_symlink_cannot_clobber_target(
        self, tmp_path: Path
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        target = tmp_path / "protected-target"
        target.write_text("keep me")
        managed_tree = tmp_path / "data" / "files"
        managed_tree.mkdir(parents=True)
        descendant = managed_tree / "managed-descendant"
        descendant.symlink_to(target)

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid="1234",
            pgid="2345",
        )

        assert result.returncode == 0, result.stderr
        assert target.read_text() == "keep me"
        assert descendant.is_symlink()
        assert "server:1234:2345:operator-arg" in log.read_text().splitlines()

    @pytest.mark.parametrize(
        ("puid", "pgid"),
        [
            pytest.param("0", "2345", id="puid-zero"),
            pytest.param("-1", "2345", id="puid-negative"),
            pytest.param("", "2345", id="puid-empty"),
            pytest.param("not-a-number", "2345", id="puid-malformed"),
            pytest.param("999999999999999999999999", "2345", id="puid-out-of-range"),
            pytest.param("1234", "0", id="pgid-zero"),
            pytest.param("1234", "-1", id="pgid-negative"),
            pytest.param("1234", "", id="pgid-empty"),
            pytest.param("1234", "not-a-number", id="pgid-malformed"),
            pytest.param("1234", "999999999999999999999999", id="pgid-out-of-range"),
        ],
    )
    def test_invalid_identity_stops_before_migration_or_server(
        self, tmp_path: Path, puid: str, pgid: str
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(
            script,
            fake_bin,
            server,
            tmp_path=tmp_path,
            puid=puid,
            pgid=pgid,
        )

        assert result.returncode != 0
        assert "positive numeric Linux user/group ID" in result.stderr
        assert not log.exists()


def _steps(log: Path) -> list[str]:
    """What ran after the identity was settled: migration and/or the server."""
    return [
        line.split(":", 1)[0]
        for line in log.read_text().splitlines()
        if line.startswith(("migration:", "server:"))
    ]


class TestDockerEntrypointRole:
    """Only the API migrates; a worker waits for the schema the API applied.

    Several worker replicas migrating at once would race, and a worker of an
    older build must never touch a schema a newer API already upgraded.
    """

    @pytest.mark.parametrize("role", [None, "all", "api"])
    def test_an_api_process_migrates_first(
        self, tmp_path: Path, role: str | None
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(script, fake_bin, server, tmp_path=tmp_path, role=role)

        assert result.returncode == 0, result.stderr
        assert _steps(log) == ["migration", "server"]

    def test_a_worker_never_migrates(self, tmp_path: Path) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        result = _run_entrypoint(
            script, fake_bin, server, tmp_path=tmp_path, role="worker"
        )

        assert result.returncode == 0, result.stderr
        assert _steps(log) == ["server"]

    def test_a_worker_still_drops_privileges(self, tmp_path: Path) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)

        _run_entrypoint(script, fake_bin, server, tmp_path=tmp_path, role="worker")

        assert log.read_text().splitlines()[-1].startswith("server:10001:10001:")


class TestWorkerBytecode:
    def test_ships_compiled_worker_application_code(self):
        dockerfile = (REPO_ROOT / "backend" / "Dockerfile").read_text()

        assert "PYTHONDONTWRITEBYTECODE=1" in dockerfile
        assert dockerfile.index(
            "COPY --chown=printstash:printstash app ./app"
        ) < dockerfile.index("RUN .venv/bin/python -m compileall -q app")
