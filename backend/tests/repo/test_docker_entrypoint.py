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

ENTRYPOINT = Path(__file__).resolve().parents[3] / "backend" / "docker-entrypoint.sh"


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
    source = source.replace("/data/db", str(tmp_path / "db"))
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
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["VAULT_DATA_DIR"] = str(tmp_path / "files")
    env["VAULT_THUMB_DIR"] = str(tmp_path / "thumbs")
    env["VAULT_STAGING_DIR"] = str(tmp_path / "staging")
    env["VAULT_BACKUP_DIR"] = str(tmp_path / "backups")
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

    def test_re_owns_every_data_root_for_the_requested_identity(
        self, tmp_path: Path
    ) -> None:
        """All five roots, not the ones a contributor remembered.

        A root left owned by the previous uid is one the container can read and
        not write, which surfaces later as a single feature failing — thumbnails,
        or backups — rather than as a startup error.
        """
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
        ownership_repair = next(
            line
            for line in log.read_text().splitlines()
            if line.startswith("chown:-hR 1234:2345 ")
        )
        for root in ("files", "thumbs", "staging", "backups", "db"):
            assert str(tmp_path / root) in ownership_repair

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
            if line.startswith("chown:-hR ")
        ]
        assert sum("1111:2222" in line for line in ownership_repairs) == 1
        assert sum("3333:4444" in line for line in ownership_repairs) == 1

    def test_managed_descendant_symlink_cannot_clobber_target(
        self, tmp_path: Path
    ) -> None:
        script, log, fake_bin, server = _entrypoint_harness(tmp_path)
        target = tmp_path / "protected-target"
        target.write_text("keep me")
        managed_tree = tmp_path / "files"
        managed_tree.mkdir()
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
