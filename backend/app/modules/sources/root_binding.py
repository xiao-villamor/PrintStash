"""Root binding."""

from __future__ import annotations

import errno
import json
import os
import secrets
import uuid
from pathlib import Path

from sqlmodel import Session

from app.core.logging import get_logger
from app.db.models import (
    ExternalLibrary,
)
from app.modules.storage.root_markers import (
    ROOT_MARKER_FILENAME,
    ROOT_MARKER_FORMAT,
    ROOT_MARKER_ROLE,
    read_root_marker_fd,
)


class ExternalRootBindingError(RuntimeError):
    """The configured external root is not the directory previously enrolled."""

    def __init__(self, state: str, message: str | None = None) -> None:
        self.state = state
        super().__init__(message or f"external_root_{state}")


def _installation_identity() -> str:
    from app.core.config import _overlay, settings

    identity = str(_overlay.get("storage_identity") or settings.storage_identity or "")
    if len(identity) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in identity
    ):
        return ""
    return identity.lower()


def _marker_payload(library: ExternalLibrary, identity: str) -> dict[str, object]:
    return {
        "format": ROOT_MARKER_FORMAT,
        "installation": identity,
        "role": ROOT_MARKER_ROLE,
        "library_id": library.id,
        "root_identity": library.root_identity,
    }


def expected_root_marker(library: ExternalLibrary) -> dict[str, object]:
    """Return the exact marker payload required for this enrolled library."""
    return _marker_payload(library, _installation_identity())


def _read_root_marker(root: Path) -> dict[str, object]:
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        return read_root_marker_fd(root_fd)
    finally:
        os.close(root_fd)


def _read_root_state(library: ExternalLibrary) -> tuple[str, str | None]:
    """Return the durable binding state without changing the filesystem."""
    if not library.root_identity:
        # A legacy row is unbound, but an orphan/conflicting marker must still
        # be surfaced instead of being presented as safely enrollable.
        root = Path(library.root_path).expanduser().resolve(strict=False)
        if root.is_dir():
            try:
                actual = _read_root_marker(root)
            except FileNotFoundError:
                actual = None
            except PermissionError:
                return "unreadable", "root_marker_unreadable"
            except OSError as exc:
                if getattr(exc, "errno", None) == errno.ELOOP:
                    return "invalid", "root_marker_invalid"
                return "unreadable", "root_marker_unreadable"
            except (ValueError, TypeError):
                return "invalid", "root_marker_invalid"
            if actual is not None:
                if (
                    isinstance(actual, dict)
                    and actual.get("format") == ROOT_MARKER_FORMAT
                    and actual.get("installation") == _installation_identity()
                    and actual.get("role") == ROOT_MARKER_ROLE
                    and actual.get("library_id") == library.id
                    and isinstance(actual.get("root_identity"), str)
                    and len(actual["root_identity"]) == 64
                    and all(
                        char in "0123456789abcdefABCDEF"
                        for char in actual["root_identity"]
                    )
                ):
                    return "unbound", "orphan_marker_requires_reenrollment"
                return "mismatch", "root_marker_conflict"
        return "unbound", "legacy_library_requires_explicit_enrollment"
    if len(library.root_identity) != 64 or any(
        char not in "0123456789abcdefABCDEF" for char in library.root_identity
    ):
        return "invalid", "invalid_root_identity"
    root = Path(library.root_path).expanduser().resolve(strict=False)
    try:
        if not root.exists() or not root.is_dir():
            return "missing", "root_path_missing"
        if not os.access(root, os.R_OK):
            return "unreadable", "root_path_unreadable"
        actual = _read_root_marker(root)
    except FileNotFoundError:
        return "missing", "root_marker_missing"
    except PermissionError:
        return "unreadable", "root_marker_unreadable"
    except OSError as exc:
        if getattr(exc, "errno", None) == errno.ELOOP:
            return "invalid", "root_marker_invalid"
        return "unreadable", "root_marker_unreadable"
    except UnicodeError:
        return "invalid", "root_marker_invalid"
    except (ValueError, TypeError):
        return "invalid", "root_marker_invalid"
    if not isinstance(actual, dict):
        return "invalid", "root_marker_invalid"
    expected = _marker_payload(library, _installation_identity())
    if actual != expected:
        return "mismatch", "root_marker_mismatch"
    return "bound", None


def root_binding_state(library: ExternalLibrary) -> tuple[str, str | None]:
    """Expose the read-only root binding probe for API and watcher callers."""
    return _read_root_state(library)


def assert_root_binding(library: ExternalLibrary) -> None:
    """Fail closed before any scan, indexing, or external write operation."""
    state, reason = _read_root_state(library)
    if state != "bound":
        raise ExternalRootBindingError(state, reason)


def _create_marker(root: Path, payload: dict[str, object]) -> bool:
    """Create a marker without replacing a file another owner supplied."""
    data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    temporary = f".{ROOT_MARKER_FILENAME}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=root_fd
        )
        try:
            written = 0
            while written < len(data):
                count = os.write(fd, data[written:])
                if count <= 0:
                    raise OSError("external root marker short write")
                written += count
            os.fsync(fd)
        finally:
            os.close(fd)
        # link() is create-only, unlike replace(), so a concurrent valid marker
        # can never be silently overwritten.
        os.link(temporary, ROOT_MARKER_FILENAME, src_dir_fd=root_fd, dst_dir_fd=root_fd)
        os.fsync(root_fd)
        return True
    except FileExistsError:
        return False
    finally:
        try:
            os.unlink(temporary, dir_fd=root_fd)
        except FileNotFoundError:
            pass
        os.close(root_fd)


def _refsync_marker(root: Path, expected: dict[str, object]) -> None:
    """Re-read and fsync an orphan marker before adopting its identity."""
    root_fd = os.open(
        root,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
    )
    try:
        actual = read_root_marker_fd(root_fd)
        if actual != expected:
            raise ValueError("root_marker_changed")
        marker_fd = os.open(
            ROOT_MARKER_FILENAME,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=root_fd,
        )
        try:
            os.fsync(marker_fd)
        finally:
            os.close(marker_fd)
        os.fsync(root_fd)
    finally:
        os.close(root_fd)


def enroll_external_root(session: Session, library: ExternalLibrary) -> ExternalLibrary:
    """Explicitly bind an existing root and commit the DB identity atomically.

    The caller must already have authenticated the operation.  The root is
    never created.  Existing markers are accepted only when they exactly match
    this library and installation; every other marker is a conflict.
    """
    root = Path(library.root_path).expanduser().resolve(strict=False)
    if not root.exists() or not root.is_dir():
        raise ExternalRootBindingError("missing", "root_path_missing")
    if not os.access(root, os.R_OK | os.W_OK):
        raise ExternalRootBindingError("unreadable", "root_path_unreadable")
    identity = _installation_identity()
    if not identity:
        raise ExternalRootBindingError("invalid", "installation_identity_missing")
    created_library_row = library.id is None
    if created_library_row:
        # Persist the row/ID first. If marker creation or its directory fsync
        # has an uncertain outcome, the operator still has a visible unbound
        # row to reconcile with the marker.
        session.add(library)
        session.flush()
        session.commit()
    library_id = library.id
    existing_identity = library.root_identity
    if existing_identity:
        state, reason = _read_root_state(library)
        if state == "bound":
            return library
        # A root can be replaced while retaining its configured path.  An
        # explicit administrator re-enrollment may adopt that markerless
        # replacement, but it must rotate the token so the old mount cannot
        # become trusted again.  Any marker (even malformed) is a conflict.
        if not (state == "missing" and reason == "root_marker_missing"):
            raise ExternalRootBindingError(state, reason)
    library.root_identity = secrets.token_hex(32)
    payload = _marker_payload(library, identity)
    created = False
    try:
        try:
            actual = _read_root_marker(root)
        except FileNotFoundError:
            actual = None
        except PermissionError as exc:
            raise ExternalRootBindingError(
                "unreadable", "root_marker_unreadable"
            ) from exc
        except OSError as exc:
            raise ExternalRootBindingError("invalid", "root_marker_invalid") from exc
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ExternalRootBindingError("invalid", "root_marker_invalid") from exc
        if actual is not None:
            # A previous enrollment whose DB commit had an unknown outcome may
            # leave our own valid marker behind. Explicit admin enrollment may
            # recover that exact marker, but never adopt another library's one.
            if (
                actual["installation"].lower() != identity
                or actual["library_id"] != library.id
            ):
                raise ExternalRootBindingError("mismatch", "root_marker_conflict")
            _refsync_marker(root, actual)
            library.root_identity = str(actual["root_identity"])
            payload = actual
        else:
            try:
                created = _create_marker(root, payload)
            except OSError as exc:
                if exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}:
                    raise ExternalRootBindingError(
                        "unreadable", "root_marker_unwritable"
                    ) from exc
                raise
            if not created:
                raise ExternalRootBindingError("mismatch", "root_marker_conflict")
        session.add(library)
        session.commit()
        session.refresh(library)
        return library
    except Exception as exc:
        session.rollback()
        if (
            created_library_row
            and not created
            and isinstance(exc, ExternalRootBindingError)
            and library_id is not None
        ):
            persisted = session.get(ExternalLibrary, library_id)
            if persisted is not None:
                session.delete(persisted)
                session.commit()
        if created:
            # A commit exception has an unknown outcome.  Preserve the marker
            # rather than unlinking by pathname: a concurrent remount or
            # replacement could make that path refer to somebody else's bytes.
            # A marker without a durable matching DB row is never trusted and
            # requires an operator-visible conflict resolution.
            logger.warning(
                "external root marker preserved after enrollment commit failure",
                extra={"library_id": library.id, "root": str(root)},
            )
        library.root_identity = existing_identity
        raise


logger = get_logger(__name__)
