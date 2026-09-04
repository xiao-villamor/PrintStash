"""Deciding whether a share can be watched for changes, or must be polled.

Filesystem watching is cheap and instant on a local disk and unreliable on a
network mount: SMB and NFS deliver change events late, partially, or not at all,
and a watcher that trusts them leaves a library looking permanently up to date
while the files under it change. Polling on a cron is slower but always correct.

So `detect_fs_kind` classifies the root and `should_watch` turns that into the
decision, and the default (`AUTO`) resolves the ambiguity the safe way: watch a
local disk, poll anything else — including `unknown`, because a root we could not
classify is exactly the case where guessing wrong costs a silently stale library.
`EVENTS` is the operator overriding that for a mount they know is well-behaved,
`OFF` is the operator turning it off entirely, and a disabled library is never
watched regardless of mode, because "disabled" has to mean no background work.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import ExternalLibrary, ExternalLibraryWatchMode
from app.services import external_library


def _library(root: Path, mode: ExternalLibraryWatchMode) -> ExternalLibrary:
    library = ExternalLibrary(name="nas", root_path=str(root))
    library.watch_mode = mode
    return library


class TestDetectFsKind:
    def test_classifies_a_root_as_one_of_the_kinds_should_watch_understands(
        self, tmp_path: Path
    ) -> None:
        # Not asserted as "local": CI runners place pytest temp files on mounted
        # Windows and network filesystems, so pinning the value would fail on the
        # very platforms the network branch exists for. What must hold is that
        # the result is always a kind `should_watch` has a rule for.
        assert external_library.detect_fs_kind(tmp_path) in {
            "local",
            "network",
            "unknown",
        }


class TestShouldWatch:
    @pytest.mark.parametrize(
        ("fs_kind", "watched"),
        [("local", True), ("network", False), ("unknown", False)],
    )
    def test_watches_only_a_local_filesystem_on_auto(
        self, tmp_path: Path, fs_kind: str, watched: bool
    ) -> None:
        library = _library(tmp_path, ExternalLibraryWatchMode.AUTO)

        assert external_library.should_watch(library, fs_kind) is watched

    def test_watches_a_network_mount_when_events_is_forced(
        self, tmp_path: Path
    ) -> None:
        library = _library(tmp_path, ExternalLibraryWatchMode.EVENTS)

        # The operator's override for a mount they know delivers events.
        assert external_library.should_watch(library, "network") is True

    def test_never_watches_when_watching_is_off(self, tmp_path: Path) -> None:
        library = _library(tmp_path, ExternalLibraryWatchMode.OFF)

        assert external_library.should_watch(library, "local") is False

    def test_never_watches_a_disabled_library(self, tmp_path: Path) -> None:
        library = _library(tmp_path, ExternalLibraryWatchMode.EVENTS)
        library.enabled = False

        # "Disabled" has to mean no background work, or a library the user
        # switched off keeps a watcher alive against their share.
        assert external_library.should_watch(library, "local") is False
