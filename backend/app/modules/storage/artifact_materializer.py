"""Verified, disposable disk representations with cross-process claims and leases.

The private SQLite index is cache metadata, never catalogue ownership. All file
selection, publication and removal is serialized with the index transaction.
Process incarnation tokens make abandoned claims recoverable without expiring a
live response merely because it is slow.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import ContextManager

from printstash_core.files import publish_staged_file


class CacheUnavailable(RuntimeError):
    """Caching cannot safely admit this representation; use normal delivery."""


class RepresentationChanged(RuntimeError):
    """The authoritative transfer does not match the expected representation."""


@dataclass(frozen=True)
class Representation:
    kind: str
    version: int
    sha256: str
    size: int

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", self.kind):
            raise ValueError("invalid representation kind")
        if (
            self.version < 1
            or self.size < 0
            or not re.fullmatch(r"[0-9a-f]{64}", self.sha256)
        ):
            raise ValueError("invalid representation identity")

    @property
    def key(self) -> str:
        return hashlib.sha256(
            f"{self.kind}:{self.version}:{self.sha256}:{self.size}".encode()
        ).hexdigest()


@dataclass(frozen=True)
class CachePolicy:
    enabled: bool = False
    max_bytes: int = 10 * 1024**3
    max_entries: int = 10000
    max_fills: int = 2
    headroom_bytes: int = 1024**3
    verify_every_hits: int = 100
    fill_wait_seconds: int = 30


def _process_identity(pid: int) -> str | None:
    try:
        # starttime disambiguates PID reuse; proc is available on supported Linux.
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


class CacheLease:
    def __init__(self, cache: ArtifactMaterializer, token: str, path: Path):
        self.path = path
        self._cache = cache
        self._token = token

    def close(self) -> None:
        if self._token:
            token, self._token = self._token, ""
            try:
                self._cache.release(token)
            except (CacheUnavailable, OSError, sqlite3.Error):
                # Preserve the durable claim if the disposable index is broken;
                # response completion must not fail because cache cleanup did.
                pass

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *_: object) -> None:
        self.close()


class ArtifactMaterializer:
    def __init__(
        self,
        root: Path,
        policy: Callable[[], CachePolicy],
        reserve: Callable[[str, Path, int], ContextManager[object]] | None = None,
        recover_reservation: Callable[[str], None] | None = None,
    ):
        self.root = root.absolute()
        self.policy = policy
        self.reserve = reserve
        self.recover_reservation = recover_reservation
        self._maintenance_lock = threading.Lock()
        self._maintenance_running = False
        self.pid = os.getpid()
        self.incarnation = _process_identity(self.pid)
        if self.incarnation is None:
            raise CacheUnavailable("process identity unavailable")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.resolve() != self.root or self.root.stat().st_uid != os.getuid():
            raise CacheUnavailable("cache root must be private")
        marker = self.root / ".printstash-artifact-cache"
        if not marker.exists():
            if any(self.root.iterdir()):
                raise CacheUnavailable("cache root is not empty or enrolled")
            with marker.open("x") as output:
                output.write("printstash-artifact-cache-v1\n")
                output.flush()
                os.fsync(output.fileno())
            self._sync_directory(self.root)
        if (
            marker.is_symlink()
            or marker.read_text() != "printstash-artifact-cache-v1\n"
        ):
            raise CacheUnavailable("cache root marker invalid")
        self.root.chmod(0o700)
        root_info = self.root.stat()
        self._root_identity = (root_info.st_dev, root_info.st_ino)
        with self._transaction() as db:
            if db.execute("PRAGMA user_version").fetchone()[0] not in (0, 1, 2):
                raise CacheUnavailable("unsupported cache index version")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS entries (
                    key TEXT PRIMARY KEY, size INTEGER NOT NULL, digest TEXT NOT NULL,
                    inode INTEGER NOT NULL, mtime INTEGER NOT NULL,
                    used REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0,
                    discarded INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS claims (
                    key TEXT PRIMARY KEY, token TEXT NOT NULL, size INTEGER NOT NULL,
                    pid INTEGER NOT NULL, incarnation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS leases (
                    token TEXT PRIMARY KEY, key TEXT NOT NULL,
                    pid INTEGER NOT NULL, incarnation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS metrics (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
            """)
            columns = {row[1] for row in db.execute("PRAGMA table_info(entries)")}
            if "representation" not in columns:
                db.execute(
                    "ALTER TABLE entries ADD COLUMN representation TEXT NOT NULL DEFAULT 'artifact'"
                )
            if "verified" not in columns:
                db.execute(
                    "ALTER TABLE entries ADD COLUMN verified REAL NOT NULL DEFAULT 0"
                )
            db.execute("PRAGMA user_version=2")
        self.reconcile()

    @staticmethod
    def _sync_directory(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _body_path(self, key: str, *, create: bool = False) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise CacheUnavailable("invalid private cache identity")
        parent = self.root
        for component in ("objects", key[:2]):
            directory = parent / component
            if create and not directory.exists():
                directory.mkdir(mode=0o700)
                self._sync_directory(parent)
            if directory.exists() and (
                directory.is_symlink()
                or not directory.is_dir()
                or directory.stat().st_uid != os.getuid()
            ):
                raise CacheUnavailable("invalid private cache directory")
            parent = directory
        return parent / f"{key}.blob"

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        info = self.root.stat(follow_symlinks=False)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_dev, info.st_ino) != self._root_identity
            or (self.root / "index.sqlite3").is_symlink()
        ):
            raise CacheUnavailable("private cache root or index replaced")
        db = sqlite3.connect(self.root / "index.sqlite3", timeout=5)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _metric(self, db: sqlite3.Connection, name: str, amount: int = 1) -> None:
        db.execute(
            "INSERT INTO metrics VALUES (?,?) ON CONFLICT(name) DO UPDATE SET value=MIN(value+excluded.value,9223372036854775806)",
            (name, amount),
        )

    def integrity_failure(self) -> None:
        logging.getLogger(__name__).warning("artifact_cache_integrity_failure")
        try:
            with self._transaction() as db:
                self._metric(db, "errors")
                self._metric(db, "corruptions")
        except (CacheUnavailable, OSError, sqlite3.Error):
            pass

    def publication_failure(self) -> None:
        logging.getLogger(__name__).warning("artifact_cache_publication_failure")
        try:
            with self._transaction() as db:
                self._metric(db, "errors")
                self._metric(db, "publication_failures")
        except (CacheUnavailable, OSError, sqlite3.Error):
            pass

    def _remove(self, db: sqlite3.Connection, key: str) -> bool:
        if db.execute("SELECT 1 FROM leases WHERE key=?", (key,)).fetchone():
            db.execute("UPDATE entries SET discarded=1 WHERE key=?", (key,))
            return False
        if re.fullmatch(r"[0-9a-f]{64}", key):
            self._body_path(key).unlink(missing_ok=True)
        db.execute("DELETE FROM entries WHERE key=?", (key,))
        self._metric(db, "evictions")
        return True

    def reconcile(self) -> None:
        with self._transaction() as db:
            for table in ("leases", "claims"):
                for row in db.execute(f"SELECT * FROM {table}").fetchall():
                    if _process_identity(row["pid"]) != row["incarnation"]:
                        if table == "claims" and self.recover_reservation:
                            self.recover_reservation(
                                str(row["token"]).removeprefix("revoked:")
                            )
                        db.execute(
                            f"DELETE FROM {table} WHERE token=?", (row["token"],)
                        )
            for row in db.execute("SELECT * FROM entries").fetchall():
                if row["discarded"] or not self._body_path(row["key"]).is_file():
                    self._remove(db, row["key"])
            known = {
                self._body_path(r[0]) for r in db.execute("SELECT key FROM entries")
            }
            known.update(
                self.root / f"{str(r[0]).removeprefix('revoked:')}.tmp"
                for r in db.execute("SELECT token FROM claims")
            )
            paths = list(self.root.iterdir())
            objects = self.root / "objects"
            if objects.is_dir() and not objects.is_symlink():
                for shard in objects.iterdir():
                    if (
                        re.fullmatch(r"[0-9a-f]{2}", shard.name)
                        and shard.is_dir()
                        and not shard.is_symlink()
                    ):
                        paths.extend(shard.iterdir())
            for path in paths:
                if (
                    re.fullmatch(r"[0-9a-f]{64}\.blob|[0-9a-f]{32}\.tmp", path.name)
                    and path not in known
                ):
                    path.unlink(missing_ok=True)

    def inspect_entries(self, *, full: bool, limit: int = 100) -> dict[str, int]:
        """Bounded disposable-cache observations, never authoritative evidence."""
        checked = corrupt = 0
        with self._transaction() as db:
            for row in db.execute(
                "SELECT * FROM entries WHERE discarded=0 ORDER BY verified,used LIMIT ?",
                (max(0, min(limit, 100)),),
            ).fetchall():
                checked += 1
                valid = bool(re.fullmatch(r"[0-9a-f]{64}", row["key"]))
                try:
                    if valid:
                        path = self._body_path(row["key"])
                        info = path.stat(follow_symlinks=False)
                        valid = (
                            stat.S_ISREG(info.st_mode)
                            and info.st_size == row["size"]
                            and info.st_ino == row["inode"]
                            and info.st_mtime_ns == row["mtime"]
                        )
                        if valid and full:
                            with path.open("rb") as source:
                                valid = (
                                    hashlib.file_digest(source, "sha256").hexdigest()
                                    == row["digest"]
                                )
                            if valid:
                                db.execute(
                                    "UPDATE entries SET verified=? WHERE key=?",
                                    (time.time(), row["key"]),
                                )
                except OSError:
                    valid = False
                if not valid:
                    corrupt += 1
                    self._remove(db, row["key"])
                    self._metric(db, "errors")
                    self._metric(db, "corruptions")
        return {"checked": checked, "corrupt": corrupt}

    def acquire(self, representation: Representation) -> CacheLease | None:
        if not self.policy().enabled:
            return None
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM entries WHERE key=? AND discarded=0",
                (representation.key,),
            ).fetchone()
            if row is None:
                self._metric(db, "misses")
                return None
            path = self._body_path(representation.key)
            try:
                info = path.stat(follow_symlinks=False)
                valid = (
                    stat.S_ISREG(info.st_mode)
                    and info.st_size == representation.size
                    and row["size"] == representation.size
                    and row["digest"] == representation.sha256
                    and row["representation"] == representation.kind
                    and info.st_ino == row["inode"]
                    and info.st_mtime_ns == row["mtime"]
                )
                sampling = self.policy().verify_every_hits
                if valid and sampling and row["hits"] % sampling == 0:
                    with path.open("rb") as stream:
                        valid = (
                            hashlib.file_digest(stream, "sha256").hexdigest()
                            == representation.sha256
                        )
                    if valid:
                        db.execute(
                            "UPDATE entries SET verified=? WHERE key=?",
                            (time.time(), representation.key),
                        )
            except OSError:
                valid = False
            if not valid:
                self._metric(db, "errors")
                self._metric(db, "corruptions")
                self._remove(db, representation.key)
                return None
            token = uuid.uuid4().hex
            db.execute(
                "INSERT INTO leases VALUES (?,?,?,?)",
                (token, representation.key, self.pid, self.incarnation),
            )
            now = time.time()
            if row["used"] < now - 60:
                db.execute(
                    "UPDATE entries SET used=? WHERE key=?",
                    (now, representation.key),
                )
            db.execute(
                "UPDATE entries SET hits=hits+1 WHERE key=?", (representation.key,)
            )
            self._metric(db, "hits")
            self._metric(db, "bytes_saved", representation.size)
            self._metric(db, f"{representation.kind}_hits")
            return CacheLease(self, token, path)

    def release(self, token: str) -> None:
        needs_trim = False
        with self._transaction() as db:
            row = db.execute(
                "SELECT key FROM leases WHERE token=?", (token,)
            ).fetchone()
            db.execute("DELETE FROM leases WHERE token=?", (token,))
            if (
                row
                and db.execute(
                    "SELECT 1 FROM entries WHERE key=? AND discarded=1", (row[0],)
                ).fetchone()
            ):
                self._remove(db, row[0])
            total = db.execute(
                "SELECT COALESCE(SUM(size),0),COUNT(*) FROM entries"
            ).fetchone()
            policy = self.policy()
            needs_trim = (
                total[0] > policy.max_bytes
                or total[1] > policy.max_entries
                or shutil.disk_usage(self.root).free < policy.headroom_bytes
            )
        if needs_trim:
            self.request_maintenance()

    def clear(self) -> dict[str, int]:
        with self._transaction() as db:
            for row in db.execute("SELECT key FROM entries").fetchall():
                self._remove(db, row[0])
            # Revoke publication, but leave live writer's temp to its own finally.
            db.execute(
                "UPDATE claims SET token='revoked:' || token WHERE token NOT LIKE 'revoked:%'"
            )
        return self.status()

    def status(self) -> dict[str, int]:
        with self._transaction() as db:
            row = db.execute(
                "SELECT COALESCE(SUM(size),0),COUNT(*) FROM entries"
            ).fetchone()
            claimed = db.execute(
                "SELECT COALESCE(SUM(size),0),COUNT(*) FROM claims"
            ).fetchone()
            metrics = {r[0]: r[1] for r in db.execute("SELECT * FROM metrics")}
            reads = metrics.get("hits", 0) + metrics.get("misses", 0)
            return {
                "bytes": row[0],
                "entries": row[1],
                "reserved_bytes": claimed[0],
                "fills": claimed[1],
                "leases": db.execute("SELECT COUNT(*) FROM leases").fetchone()[0],
                "pending_eviction_bytes": max(
                    0,
                    row[0] + claimed[0] - self.policy().max_bytes,
                    db.execute(
                        "SELECT COALESCE(SUM(size),0) FROM entries WHERE discarded=1"
                    ).fetchone()[0],
                ),
                "maintenance_running": int(self._maintenance_running),
                "last_verification": int(
                    db.execute(
                        "SELECT COALESCE(MAX(verified),0) FROM entries"
                    ).fetchone()[0]
                ),
                "hit_ratio_percent": int(metrics.get("hits", 0) * 100 / reads)
                if reads
                else 0,
                **metrics,
            }

    def health(self) -> dict[str, bool | str]:
        if not self.policy().enabled:
            return {"ok": True, "state": "disabled"}
        try:
            root_mode = self.root.stat().st_mode
            if not os.access(self.root, os.W_OK) or not root_mode & 0o222:
                return {"ok": False, "state": "unwritable"}
            usage = self.status()
            policy = self.policy()
            full = (
                usage["bytes"] + usage["reserved_bytes"] >= policy.max_bytes
                or usage["entries"] >= policy.max_entries
                or shutil.disk_usage(self.root).free < policy.headroom_bytes
            )
            return {"ok": not full, "state": "full" if full else "ready"}
        except sqlite3.DatabaseError:
            return {"ok": False, "state": "corrupt_index"}
        except (OSError, CacheUnavailable):
            return {"ok": False, "state": "unavailable"}

    def trim(self) -> None:
        """Evict only idle paths until live byte/count/headroom targets hold."""
        with self._transaction() as db:
            policy = self.policy()
            claims = db.execute(
                "SELECT COALESCE(SUM(size),0),COUNT(*) FROM claims"
            ).fetchone()
            total = list(
                db.execute(
                    "SELECT COALESCE(SUM(size),0),COUNT(*) FROM entries"
                ).fetchone()
            )
            for row in db.execute(
                "SELECT key,size FROM entries ORDER BY discarded DESC,used"
            ).fetchall():
                if (
                    total[0] + claims[0] <= policy.max_bytes
                    and total[1] + claims[1] <= policy.max_entries
                    and shutil.disk_usage(self.root).free - claims[0]
                    >= policy.headroom_bytes
                ):
                    break
                # A limit reduction schedules idle eviction, never revokes live
                # leases or counts an open file as reclaimed.
                if not db.execute(
                    "SELECT 1 FROM leases WHERE key=?", (row[0],)
                ).fetchone():
                    if self._remove(db, row[0]):
                        total[0] -= row[1]
                        total[1] -= 1

    def request_maintenance(self) -> None:
        if not self._maintenance_lock.acquire(blocking=False):
            return
        self._maintenance_running = True

        def maintain():
            try:
                self.trim()
            except (CacheUnavailable, OSError, sqlite3.Error):
                pass
            finally:
                self._maintenance_running = False
                self._maintenance_lock.release()

        threading.Thread(
            target=maintain, name="artifact-cache-trim", daemon=True
        ).start()

    def begin_fill(self, representation: Representation) -> CacheFill | None:
        policy = self.policy()
        if not policy.enabled or representation.size > policy.max_bytes:
            with self._transaction() as db:
                self._metric(db, "bypasses")
            return None
        with self._transaction() as db:
            if (
                db.execute(
                    "SELECT 1 FROM entries WHERE key=?", (representation.key,)
                ).fetchone()
                or db.execute(
                    "SELECT 1 FROM claims WHERE key=?", (representation.key,)
                ).fetchone()
            ):
                return None
            claims = db.execute(
                "SELECT COALESCE(SUM(size),0),COUNT(*) FROM claims"
            ).fetchone()
            if claims[1] >= policy.max_fills:
                self._metric(db, "bypasses")
                return None
            total = list(
                db.execute(
                    "SELECT COALESCE(SUM(size),0),COUNT(*) FROM entries"
                ).fetchone()
            )
            for row in db.execute(
                "SELECT key,size FROM entries ORDER BY used"
            ).fetchall():
                if (
                    total[0] + claims[0] + representation.size <= policy.max_bytes
                    and total[1] + claims[1] < policy.max_entries
                    and shutil.disk_usage(self.root).free
                    - claims[0]
                    - representation.size
                    >= policy.headroom_bytes
                ):
                    break
                if self._remove(db, row[0]):
                    total[0] -= row[1]
                    total[1] -= 1
            if (
                total[0] + claims[0] + representation.size > policy.max_bytes
                or total[1] + claims[1] >= policy.max_entries
                or shutil.disk_usage(self.root).free - claims[0] - representation.size
                < policy.headroom_bytes
            ):
                self._metric(db, "bypasses")
                return None
            token = uuid.uuid4().hex
            db.execute(
                "INSERT INTO claims VALUES (?,?,?,?,?)",
                (
                    representation.key,
                    token,
                    representation.size,
                    self.pid,
                    self.incarnation,
                ),
            )
        return CacheFill(self, representation, token)

    def filling(self, representation: Representation) -> bool:
        with self._transaction() as db:
            return (
                db.execute(
                    "SELECT 1 FROM claims WHERE key=?", (representation.key,)
                ).fetchone()
                is not None
            )

    @contextmanager
    def materialize(
        self, representation: Representation, chunks: Callable[[], Iterator[bytes]]
    ) -> Iterator[Path]:
        # Wait only on the same representation, never hold a database lock over IO.
        deadline = time.monotonic() + self.policy().fill_wait_seconds
        while True:
            lease = self.acquire(representation)
            if lease:
                with lease as path:
                    yield path
                return
            fill = self.begin_fill(representation)
            if fill:
                try:
                    for chunk in chunks():
                        fill.write(chunk)
                    try:
                        lease = fill.complete()
                    except (CacheUnavailable, OSError, sqlite3.Error):
                        if not fill.verified:
                            raise
                        self.publication_failure()
                        lease = None
                    if lease is not None:
                        with lease as path:
                            yield path
                    else:
                        # The exact verified download is usable even if optional
                        # publication fails. Keep its claim and reservation until
                        # this consumer closes, including during clear-cache.
                        yield fill.path
                finally:
                    fill.close()
                return
            if not self.filling(representation) or time.monotonic() >= deadline:
                # Publication can win between acquire and begin_fill. Recheck
                # before interpreting an absent claim as admission refusal.
                lease = self.acquire(representation)
                if lease:
                    with lease as path:
                        yield path
                    return
                raise CacheUnavailable("cache admission refused")
            threading.Event().wait(0.02)


class CacheFill:
    def __init__(
        self, cache: ArtifactMaterializer, representation: Representation, token: str
    ):
        self.cache = cache
        self.representation = representation
        self.token = token
        self.path = cache.root / f"{token}.tmp"
        self.output = None
        self.reservation = None
        self.digest = hashlib.sha256()
        self.size = 0
        self.verified = False
        try:
            if cache.reserve:
                self.reservation = cache.reserve(token, cache.root, representation.size)
                self.reservation.__enter__()
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self.output = os.fdopen(fd, "wb")
        except BaseException:
            self.close()
            raise

    def write(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.representation.size:
            self.cache.integrity_failure()
            raise RepresentationChanged("representation size mismatch")
        if self.output is None:
            raise CacheUnavailable("fill closed")
        if (
            shutil.disk_usage(self.cache.root).free - len(chunk)
            < self.cache.policy().headroom_bytes
        ):
            raise CacheUnavailable("cache free-space headroom exhausted")
        self.output.write(chunk)
        self.digest.update(chunk)

    def complete(self) -> CacheLease | None:
        if (
            self.size != self.representation.size
            or self.digest.hexdigest() != self.representation.sha256
        ):
            self.cache.integrity_failure()
            raise RepresentationChanged("representation digest mismatch")
        if self.output is None:
            raise CacheUnavailable("fill closed")
        self.output.flush()
        os.fsync(self.output.fileno())
        self.output.close()
        self.output = None
        self.verified = True
        destination: Path | None = None
        linked = False
        try:
            with self.cache._transaction() as db:
                policy = self.cache.policy()
                total = db.execute(
                    "SELECT COALESCE(SUM(size),0),COUNT(*) FROM entries"
                ).fetchone()
                claims = db.execute(
                    "SELECT COALESCE(SUM(size),0),COUNT(*) FROM claims"
                ).fetchone()
                if (
                    not policy.enabled
                    or total[0] + claims[0] > policy.max_bytes
                    or total[1] + claims[1] > policy.max_entries
                    or shutil.disk_usage(self.cache.root).free < policy.headroom_bytes
                    or not db.execute(
                        "SELECT 1 FROM claims WHERE token=?", (self.token,)
                    ).fetchone()
                ):
                    return None
                destination = self.cache._body_path(
                    self.representation.key, create=True
                )
                # Publication never replaces an existing path. The index stays
                # uncommitted until a hardlinkless copy is complete and synced.
                # Keep the verified temp for fallback without another transfer.
                publish_staged_file(self.path, destination)
                linked = True
                self.cache._sync_directory(destination.parent)
                info = destination.stat()
                now = time.time()
                db.execute(
                    "INSERT INTO entries(key,size,digest,inode,mtime,used,representation,verified) VALUES (?,?,?,?,?,?,?,?)",
                    (
                        self.representation.key,
                        self.size,
                        self.representation.sha256,
                        info.st_ino,
                        info.st_mtime_ns,
                        now,
                        self.representation.kind,
                        now,
                    ),
                )
                db.execute("DELETE FROM claims WHERE token=?", (self.token,))
                lease_token = uuid.uuid4().hex
                db.execute(
                    "INSERT INTO leases VALUES (?,?,?,?)",
                    (
                        lease_token,
                        self.representation.key,
                        self.cache.pid,
                        self.cache.incarnation,
                    ),
                )
                self.cache._metric(db, "completed_fills")
                return CacheLease(self.cache, lease_token, destination)
        except BaseException:
            if linked and destination is not None:
                try:
                    destination.unlink(missing_ok=True)
                    self.cache._sync_directory(destination.parent)
                except OSError:
                    # Startup reconciliation removes an exact unindexed body if
                    # prompt cleanup is itself unavailable.
                    pass
            raise

    def close(self) -> None:
        try:
            if self.output:
                self.output.close()
                self.output = None
            self.path.unlink(missing_ok=True)
            with self.cache._transaction() as db:
                db.execute(
                    "DELETE FROM claims WHERE token IN (?,?)",
                    (self.token, f"revoked:{self.token}"),
                )
        except (CacheUnavailable, OSError, sqlite3.Error):
            # Reconciliation owns exact leftover files/claims. A broken cache
            # index must not break otherwise safe source streaming.
            pass
        finally:
            if self.reservation:
                self.reservation.__exit__(None, None, None)
                self.reservation = None
