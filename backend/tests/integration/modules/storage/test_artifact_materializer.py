"""Cached paths stay verified and available until their last consumer closes."""

from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest

from app.modules.storage.artifact_materializer import (
    ArtifactMaterializer,
    CachePolicy,
    CacheUnavailable,
    Representation,
    RepresentationChanged,
)


@pytest.fixture
def cache(tmp_path):
    return ArtifactMaterializer(
        tmp_path / "cache", lambda: CachePolicy(enabled=True, headroom_bytes=0)
    )


@pytest.fixture
def representation():
    payload = b"verified bytes"
    return Representation(
        "artifact", 1, hashlib.sha256(payload).hexdigest(), len(payload)
    )


class TestRepresentation:
    @pytest.mark.parametrize(
        "representation",
        [
            pytest.param(("Artifact", 1, "a" * 64, 1), id="uppercase-kind"),
            pytest.param(("artifact", 0, "a" * 64, 1), id="zero-version"),
            pytest.param(("artifact", 1, "a" * 63, 1), id="short-digest"),
            pytest.param(("artifact", 1, "a" * 64, -1), id="negative-size"),
        ],
    )
    def test_rejects_malformed_representation_identity(self, representation):
        with pytest.raises(ValueError, match="invalid representation"):
            Representation(*representation)


class TestArtifactMaterializer:
    def test_reuses_verified_materialization(self, cache, representation):
        transferred = bytearray()

        def source():
            transferred.extend(b"verified bytes")
            yield b"verified bytes"

        with cache.materialize(representation, source) as path:
            assert path.read_bytes() == b"verified bytes"
        with cache.materialize(representation, source) as path:
            assert path.read_bytes() == b"verified bytes"
        assert transferred == b"verified bytes"

    def test_preserves_selection_before_open(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        lease = cache.acquire(representation)
        cache.clear()
        assert lease.path.read_bytes() == b"verified bytes"
        lease.close()
        assert not lease.path.exists()

    def test_accounts_for_bytes_until_lease_release(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            assert cache.clear()["bytes"] == representation.size
        assert cache.status()["bytes"] == 0

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param(b"short", id="short"),
            pytest.param(b"incorrect byte", id="digest"),
            pytest.param(b"way too many unexpected bytes", id="oversize"),
        ],
    )
    def test_rejects_corrupt_fill(self, cache, representation, payload):
        with pytest.raises(RepresentationChanged, match="representation"):
            with cache.materialize(representation, lambda: iter([payload])):
                pytest.fail("exposed corrupt content")
        assert cache.status()["entries"] == 0
        assert list(cache.root.glob("*.tmp")) == []

    def test_discards_interrupted_fill(self, cache, representation):
        fill = cache.begin_fill(representation)
        fill.write(b"verified")
        fill.close()
        assert cache.acquire(representation) is None
        assert cache.status()["reserved_bytes"] == 0

    def test_rejects_corrupt_hit(self, cache, representation):
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            pass
        path.write_bytes(b"incorrect byte")
        assert cache.acquire(representation) is None
        assert cache.status()["errors"] == 1

    def test_separates_representation_versions(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        assert cache.acquire(replace(representation, version=2)) is None

    def test_clear_revokes_active_fill(self, cache, representation):
        fill = cache.begin_fill(representation)
        fill.write(b"verified bytes")
        cache.clear()
        assert fill.complete() is None
        fill.close()
        assert cache.status()["entries"] == 0

    def test_disable_preserves_existing_lease(self, cache, representation):
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            cache.policy = lambda: CachePolicy(enabled=False)
            assert path.read_bytes() == b"verified bytes"
            assert cache.begin_fill(replace(representation, version=2)) is None
        assert cache.status()["leases"] == 0

    def test_bounds_bytes_while_leased(self, cache, representation):
        cache.policy = lambda: CachePolicy(
            enabled=True, max_bytes=representation.size, headroom_bytes=0
        )
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            assert cache.begin_fill(replace(representation, version=2)) is None
        assert cache.status()["bytes"] <= representation.size

    def test_bounds_concurrent_fill_count(self, cache, representation):
        cache.policy = lambda: CachePolicy(enabled=True, max_fills=1, headroom_bytes=0)
        fill = cache.begin_fill(representation)
        assert cache.begin_fill(replace(representation, version=2)) is None
        fill.close()

    def test_recovers_abandoned_claim(self, cache, representation):
        fill = cache.begin_fill(representation)
        fill.write(b"verified")
        fill.output.close()
        fill.output = None
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            db.execute("UPDATE claims SET incarnation='previous-process'")
        cache.reconcile()
        assert cache.status()["reserved_bytes"] == 0
        assert not fill.path.exists()

    def test_recovers_abandoned_lease(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            lease = cache.acquire(representation)
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            db.execute("UPDATE leases SET incarnation='previous-process'")
        cache.reconcile()
        assert cache.clear()["bytes"] == 0
        assert not lease.path.exists()

    def test_recovers_unindexed_publication(self, cache, representation):
        orphan = cache.root / f"{representation.key}.blob"
        orphan.write_bytes(b"verified bytes")
        cache.reconcile()
        assert not orphan.exists()

    def test_coalesces_concurrent_materialization(self, cache, representation):
        entered = Event()
        release = Event()
        transferred = bytearray()

        def source():
            transferred.extend(b"verified bytes")
            entered.set()
            assert release.wait(5)
            yield b"verified bytes"

        def read():
            with cache.materialize(representation, source) as path:
                return path.read_bytes()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(read)
            assert entered.wait(5)
            second = pool.submit(read)
            release.set()
            assert first.result() == second.result() == b"verified bytes"
        assert transferred == b"verified bytes"

    def test_disabled_cache_refuses_materialization(self, cache, representation):
        cache.policy = lambda: CachePolicy(enabled=False)
        with pytest.raises(CacheUnavailable, match="admission refused"):
            with cache.materialize(representation, lambda: iter([b"verified bytes"])):
                pytest.fail("disabled cache exposed a path")

    def test_shrinking_policy_revokes_oversized_fill(self, cache, representation):
        fill = cache.begin_fill(representation)
        fill.write(b"verified bytes")
        cache.policy = lambda: CachePolicy(enabled=True, max_bytes=1, headroom_bytes=0)
        assert fill.complete() is None
        fill.close()
        assert cache.status()["entries"] == 0

    def test_evicts_idle_entries_for_admission(self, cache, representation):
        cache.policy = lambda: CachePolicy(
            enabled=True, max_entries=1, headroom_bytes=0
        )
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        replacement = replace(representation, version=2)
        with cache.materialize(replacement, lambda: iter([b"verified bytes"])) as path:
            assert path.read_bytes() == b"verified bytes"
        assert cache.acquire(representation) is None
        assert cache.status()["entries"] == 1

    def test_refuses_unenrolled_nonempty_root(self, tmp_path):
        existing = tmp_path / "user-file"
        existing.write_bytes(b"keep")
        with pytest.raises(CacheUnavailable, match="not empty"):
            ArtifactMaterializer(tmp_path, lambda: CachePolicy())
        assert existing.read_bytes() == b"keep"

    def test_reconciliation_preserves_live_fill(self, cache, representation):
        fill = cache.begin_fill(representation)
        fill.write(b"verified bytes")
        cache.clear()
        cache.reconcile()
        assert fill.path.exists()
        fill.close()

    def test_rehash_rejects_corruption_with_preserved_metadata(
        self, cache, representation
    ):
        import os

        cache.policy = lambda: CachePolicy(
            enabled=True, headroom_bytes=0, verify_every_hits=1
        )
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            pass
        before = path.stat()
        path.write_bytes(b"incorrect byte")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert cache.acquire(representation) is None

    def test_fill_uses_bounded_memory(self, cache):
        import tracemalloc

        chunk = b"x" * 65536
        digest = hashlib.sha256()
        for _ in range(1024):
            digest.update(chunk)
        representation = Representation("artifact", 1, digest.hexdigest(), 64 * 1024**2)
        tracemalloc.start()
        try:
            with cache.materialize(
                representation, lambda: (chunk for _ in range(1024))
            ) as path:
                assert path.stat().st_size == representation.size
            _, peak = tracemalloc.get_traced_memory()
            assert peak < 8 * 1024**2
        finally:
            tracemalloc.stop()

    def test_cache_audit_discards_corrupt_representation(self, cache, representation):
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            pass
        path.write_bytes(b"incorrect byte")
        assert cache.inspect_entries(full=True) == {"checked": 1, "corrupt": 1}
        assert cache.status()["entries"] == 0

    def test_cache_audit_accepts_verified_representation(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        assert cache.inspect_entries(full=True) == {"checked": 1, "corrupt": 0}

    def test_cache_audit_preserves_leased_corrupt_entry(self, cache, representation):
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            path.write_bytes(b"incorrect byte")
            assert cache.inspect_entries(full=False)["corrupt"] == 1
            assert cache.status()["bytes"] == representation.size
        assert cache.status()["bytes"] == 0


class TestCacheCompletionContract:
    def test_caches_verified_bytes_without_hardlinks(
        self, cache, representation, monkeypatch
    ):
        import errno
        import os

        def unsupported(*args, **kwargs):
            raise OSError(errno.EPERM, "no hard links")

        monkeypatch.setattr(os, "link", unsupported)
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            assert path.read_bytes() == b"verified bytes"
            assert path.suffix == ".blob"
        assert cache.status()["entries"] == 1

    def test_shards_private_representation_files(self, cache, representation):
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            assert path.relative_to(cache.root).parts == (
                "objects",
                representation.key[:2],
                f"{representation.key}.blob",
            )
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700

    def test_never_replaces_an_existing_publication(self, cache, representation):
        destination = (
            cache.root
            / "objects"
            / representation.key[:2]
            / f"{representation.key}.blob"
        )
        destination.parent.mkdir(parents=True)
        destination.write_bytes(b"existing private object")
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            assert path.read_bytes() == b"verified bytes"
            assert destination.read_bytes() == b"existing private object"

    def test_uses_verified_temp_when_publication_fails(
        self, cache, representation, monkeypatch
    ):
        import os

        def fail_publish(*args, **kwargs):
            raise OSError("publication unavailable")

        monkeypatch.setattr(os, "link", fail_publish)
        transferred = []

        def source():
            transferred.append(1)
            yield b"verified bytes"

        with cache.materialize(representation, source) as path:
            assert path.read_bytes() == b"verified bytes"
            assert path.suffix == ".tmp"
            assert cache.status()["reserved_bytes"] == representation.size
        assert transferred == [1]
        assert not path.exists()
        assert cache.status()["entries"] == 0
        assert cache.status()["publication_failures"] == 1
        assert cache.status()["errors"] == 1

    def test_removes_publication_when_index_commit_fails(
        self, cache, representation, monkeypatch
    ):
        from contextlib import contextmanager

        fill = cache.begin_fill(representation)
        fill.write(b"verified bytes")
        transaction = cache._transaction

        @contextmanager
        def fail_commit():
            with transaction() as db:
                yield db
                raise sqlite3.OperationalError("commit unavailable")

        monkeypatch.setattr(cache, "_transaction", fail_commit)
        with pytest.raises(sqlite3.OperationalError, match="commit unavailable"):
            fill.complete()
        assert list((cache.root / "objects").rglob("*.blob")) == []
        assert fill.path.read_bytes() == b"verified bytes"
        fill.close()

    def test_batches_last_access_timestamp_updates(
        self, cache, representation, monkeypatch
    ):
        from app.modules.storage import artifact_materializer

        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            initial = db.execute("SELECT used FROM entries").fetchone()[0]

        monkeypatch.setattr(artifact_materializer.time, "time", lambda: initial + 1)
        lease = cache.acquire(representation)
        lease.close()
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            assert db.execute("SELECT used FROM entries").fetchone()[0] == initial

        monkeypatch.setattr(artifact_materializer.time, "time", lambda: initial + 61)
        lease = cache.acquire(representation)
        lease.close()
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            assert db.execute("SELECT used FROM entries").fetchone()[0] == initial + 61

    def test_reclaims_idle_files_to_restore_headroom(
        self, cache, representation, monkeypatch
    ):
        import shutil
        from collections import namedtuple

        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        usage = namedtuple("Usage", "total used free")
        monkeypatch.setattr(
            shutil,
            "disk_usage",
            lambda path: (
                usage(100, 90, 10)
                if list(cache.root.rglob("*.blob"))
                else usage(100, 0, 100)
            ),
        )
        cache.policy = lambda: CachePolicy(enabled=True, headroom_bytes=50)
        cache.trim()
        assert cache.status()["bytes"] == 0
        assert cache.health()["ok"] is True

    def test_health_distinguishes_corrupt_index(self, cache):
        (cache.root / "index.sqlite3").write_bytes(b"invalid index")
        assert cache.health() == {"ok": False, "state": "corrupt_index"}

    def test_reports_saved_bytes_with_verification_time(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        usage = cache.status()
        assert usage["bytes_saved"] == representation.size
        assert usage["hit_ratio_percent"] == 50
        assert usage["last_verification"] > 0

    def test_fill_stops_when_headroom_is_lost(self, cache, representation, monkeypatch):
        import shutil
        from collections import namedtuple

        fill = cache.begin_fill(representation)
        usage = namedtuple("Usage", "total used free")
        monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(100, 100, 0))
        try:
            with pytest.raises(CacheUnavailable, match="headroom"):
                fill.write(b"verified bytes")
        finally:
            fill.close()
        assert cache.status()["entries"] == 0


class TestPrivateCacheRecovery:
    def test_recovers_after_wholesale_deletion(self, cache, representation):
        import shutil

        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        shutil.rmtree(cache.root)
        assert cache.health() == {"ok": False, "state": "unavailable"}
        fresh = ArtifactMaterializer(cache.root, cache.policy)
        assert fresh.status()["entries"] == 0
        with fresh.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            assert path.read_bytes() == b"verified bytes"

    def test_replaced_root_cannot_supply_or_delete_another_directory(
        self, cache, representation
    ):
        original = cache.root.with_name("original")
        cache.root.rename(original)
        cache.root.mkdir()
        sentinel = cache.root / "sentinel"
        sentinel.write_text("user data")
        with pytest.raises(CacheUnavailable, match="replaced"):
            cache.clear()
        assert sentinel.read_text() == "user data"
        assert cache.health() == {"ok": False, "state": "unavailable"}

    def test_symlink_index_is_never_opened(self, cache, tmp_path):
        other = tmp_path / "other.sqlite3"
        other.write_bytes(b"not our database")
        index = cache.root / "index.sqlite3"
        index.unlink()
        index.symlink_to(other)
        with pytest.raises(CacheUnavailable, match="replaced"):
            cache.status()
        assert other.read_bytes() == b"not our database"

    def test_bounded_full_inspection_rotates_verified_entries(
        self, cache, representation
    ):
        for version in (1, 2):
            with cache.materialize(
                replace(representation, version=version),
                lambda: iter([b"verified bytes"]),
            ):
                pass
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            db.execute("UPDATE entries SET verified=0")
        assert cache.inspect_entries(full=True, limit=1) == {"checked": 1, "corrupt": 0}
        assert cache.inspect_entries(full=True, limit=1) == {"checked": 1, "corrupt": 0}
        with sqlite3.connect(cache.root / "index.sqlite3") as db:
            assert (
                db.execute("SELECT COUNT(*) FROM entries WHERE verified>0").fetchone()[
                    0
                ]
                == 2
            )

    def test_maintenance_returns_before_unlink_finishes(
        self, cache, representation, monkeypatch
    ):
        from pathlib import Path

        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        entered, proceed = Event(), Event()
        original_unlink = Path.unlink

        def slow_unlink(path, *args, **kwargs):
            if path.suffix == ".blob":
                entered.set()
                assert proceed.wait(5)
            return original_unlink(path, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", slow_unlink)
        cache.policy = lambda: CachePolicy(enabled=True, max_bytes=0, headroom_bytes=0)
        try:
            cache.request_maintenance()
            assert entered.wait(5)
            assert cache._maintenance_running
        finally:
            proceed.set()
        assert cache._maintenance_lock.acquire(timeout=5)
        cache._maintenance_lock.release()
        assert cache.status()["bytes"] == 0

    def test_representation_identity_cannot_select_original_entry(
        self, cache, representation
    ):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        assert cache.acquire(replace(representation, kind="derived")) is None
        assert cache.acquire(replace(representation, sha256="f" * 64)) is None

    def test_restarted_index_reuses_verified_entry(self, cache, representation):
        with cache.materialize(representation, lambda: iter([b"verified bytes"])):
            pass
        restarted = ArtifactMaterializer(cache.root, cache.policy)
        with restarted.materialize(
            representation, lambda: pytest.fail("unexpected provider read")
        ) as path:
            assert path.read_bytes() == b"verified bytes"

    def test_unlinked_open_file_stays_accounted_until_lease_release(
        self, cache, representation
    ):
        with cache.materialize(
            representation, lambda: iter([b"verified bytes"])
        ) as path:
            with path.open("rb") as opened:
                path.unlink()
                assert cache.clear()["bytes"] == representation.size
                assert opened.read() == b"verified bytes"
        assert cache.status()["bytes"] == 0
