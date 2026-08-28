"""Filesystem classification keeps watch decisions conservative.

The detector is the shared boundary used by storage safety and external-library
watching. These tests feed it mountinfo records so local, network, unknown, and
malformed mounts are all classified without depending on the runner's mounts.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path

import pytest

from app.services import filesystem


@pytest.fixture
def mountinfo(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    records: list[str] = []

    def fake_open(path: str, *, encoding: str) -> StringIO:
        assert path == "/proc/self/mountinfo"
        assert encoding == "utf-8"
        return StringIO("".join(records))

    monkeypatch.setattr("builtins.open", fake_open)
    return records


class TestDetectFsKind:
    def test_returns_unknown_when_mountinfo_cannot_be_read(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        def fail_open(*_args: object, **_kwargs: object) -> StringIO:
            raise OSError("proc unavailable")

        monkeypatch.setattr("builtins.open", fail_open)

        assert filesystem.detect_fs_kind(tmp_path) == "unknown"

    def test_returns_unknown_when_no_mount_contains_the_path(
        self, mountinfo: list[str], tmp_path: Path
    ) -> None:
        mountinfo.append("36 29 0:32 / /srv rw - ext4 /dev/root rw\n")

        assert filesystem.detect_fs_kind(tmp_path / "outside") == "unknown"

    def test_ignores_malformed_mountinfo_records(
        self, mountinfo: list[str], tmp_path: Path
    ) -> None:
        mountinfo.extend(
            [
                "not a mount record\n",
                "1 2 3\n",
                "1 2 3 4 /tmp rw -\n",
            ]
        )

        assert filesystem.detect_fs_kind(tmp_path) == "unknown"

    @pytest.mark.parametrize(
        "fstype",
        sorted(filesystem._LOCAL_FSTYPES),
        ids=lambda fstype: fstype,
    )
    def test_classifies_known_local_filesystems(
        self, mountinfo: list[str], tmp_path: Path, fstype: str
    ) -> None:
        mountinfo.append(f"36 29 0:32 / / rw - {fstype} /dev/root rw\n")

        assert filesystem.detect_fs_kind(tmp_path) == "local"

    @pytest.mark.parametrize(
        "fstype",
        [*sorted(filesystem._NETWORK_FSTYPES), "smb3.foo", "cifs.foo"],
        ids=lambda fstype: fstype,
    )
    def test_classifies_known_network_filesystems(
        self, mountinfo: list[str], tmp_path: Path, fstype: str
    ) -> None:
        mountinfo.append(f"36 29 0:32 / / rw - {fstype} server:/share rw\n")

        assert filesystem.detect_fs_kind(tmp_path) == "network"

    def test_classifies_an_unrecognised_filesystem_as_unknown(
        self, mountinfo: list[str], tmp_path: Path
    ) -> None:
        mountinfo.append("36 29 0:32 / / rw - fuse.custom /dev/fuse rw\n")

        assert filesystem.detect_fs_kind(tmp_path) == "unknown"

    def test_uses_the_most_specific_mount_for_a_nested_path(
        self, mountinfo: list[str]
    ) -> None:
        mountinfo.extend(
            [
                "36 29 0:32 / / rw - ext4 /dev/root rw\n",
                "37 29 0:33 / /srv rw - nfs4 server:/share rw\n",
            ]
        )

        assert filesystem.detect_fs_kind("/srv/models") == "network"

    def test_keeps_the_last_record_when_mount_points_have_equal_length(
        self, mountinfo: list[str]
    ) -> None:
        mountinfo.extend(
            [
                "36 29 0:32 / /srv rw - ext4 /dev/root rw\n",
                "37 29 0:33 / /srv rw - nfs4 server:/share rw\n",
            ]
        )

        assert filesystem.detect_fs_kind("/srv/models") == "network"
