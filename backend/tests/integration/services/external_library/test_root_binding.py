"""Durable external-root identity and fail-closed mutation tests."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.db.models import File
from app.db.scopes import live
from app.services import external_library, runtime_config
from app.services.ingestion import resolve_write_target
from tests.factories import build_external_library, build_file, build_model
from tests.integration.services.external_library._helpers import drop_gcode


def _enable_feature(session: Session) -> None:
    runtime_config.set_external_libraries_enabled(session, True)


class TestExternalRootBinding:
    def test_changed_source_is_rejected_before_thumbnail_processing(
        self, tmp_path: Path
    ) -> None:
        root = tmp_path / "nas"
        root.mkdir()
        source = root / "model.stl"
        source.write_bytes(b"original-mesh")
        original = source.stat()
        parent_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        pinned = external_library._PinnedFile(
            path=str(source),
            name=source.name,
            parent_fd=parent_fd,
            size=original.st_size,
            mtime=original.st_mtime,
            device=original.st_dev,
            inode=original.st_ino,
        )
        source.write_bytes(b"replacement-mesh-is-different")

        try:
            with pytest.raises(external_library.ExternalRootBindingError) as caught:
                with external_library._open_pinned_file(pinned):
                    raise AssertionError("changed source reached thumbnail processing")
        finally:
            os.close(parent_fd)

        assert caught.value.state == "mismatch"
        assert str(caught.value) == "external_file_changed"

    def test_matching_orphan_marker_requires_explicit_reenrollment(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="orphan-marker"
        )
        marker = {
            "format": external_library.ROOT_MARKER_FORMAT,
            "installation": "a" * 64,
            "role": external_library.ROOT_MARKER_ROLE,
            "library_id": library.id,
            "root_identity": "c" * 64,
        }
        (root / external_library.ROOT_MARKER_FILENAME).write_text(
            json.dumps(marker), encoding="utf-8"
        )

        assert external_library.root_binding_state(library) == (
            "unbound",
            "orphan_marker_requires_reenrollment",
        )

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            pytest.param(
                PermissionError("denied"),
                ("unreadable", "root_marker_unreadable"),
                id="permission",
            ),
            pytest.param(
                OSError(errno.ELOOP, "symlink loop"),
                ("invalid", "root_marker_invalid"),
                id="symlink-loop",
            ),
            pytest.param(
                OSError(errno.EIO, "transport error"),
                ("unreadable", "root_marker_unreadable"),
                id="io-error",
            ),
            pytest.param(
                ValueError("invalid json"),
                ("invalid", "root_marker_invalid"),
                id="invalid-payload",
            ),
        ],
    )
    def test_legacy_marker_read_failure_stays_fail_closed(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
        expected: tuple[str, str],
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="unreadable-marker"
        )

        def fail(_root: Path) -> dict[str, object]:
            raise failure

        monkeypatch.setattr(external_library, "_read_root_marker", fail)

        assert external_library.root_binding_state(library) == expected

    def test_bound_marker_with_invalid_encoding_stays_untrusted(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="invalid-encoding")

        def fail(_root: Path) -> dict[str, object]:
            raise UnicodeError("invalid marker encoding")

        monkeypatch.setattr(external_library, "_read_root_marker", fail)

        assert external_library.root_binding_state(library) == (
            "invalid",
            "root_marker_invalid",
        )

    @pytest.mark.parametrize(
        ("failure", "expected"),
        [
            pytest.param(
                PermissionError("denied"),
                ("unreadable", "root_marker_unreadable"),
                id="permission",
            ),
            pytest.param(
                OSError(errno.ELOOP, "symlink loop"),
                ("invalid", "root_marker_invalid"),
                id="symlink-loop",
            ),
            pytest.param(
                OSError(errno.EIO, "transport error"),
                ("unreadable", "root_marker_unreadable"),
                id="io-error",
            ),
            pytest.param(
                ValueError("invalid payload"),
                ("invalid", "root_marker_invalid"),
                id="invalid-payload",
            ),
        ],
    )
    def test_bound_marker_read_failure_stays_fail_closed(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
        expected: tuple[str, str],
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="bound-failure")

        def fail(_root: Path) -> dict[str, object]:
            raise failure

        monkeypatch.setattr(external_library, "_read_root_marker", fail)

        assert external_library.root_binding_state(library) == expected

    def test_invalid_durable_token_never_trusts_the_configured_root(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="invalid-token")
        library.root_identity = "short"
        db_session.add(library)
        db_session.commit()

        assert external_library.root_binding_state(library) == (
            "invalid",
            "invalid_root_identity",
        )

    def test_unreadable_bound_root_never_reaches_its_marker(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="unreadable-root")
        monkeypatch.setattr(external_library.os, "access", lambda *_args: False)

        assert external_library.root_binding_state(library) == (
            "unreadable",
            "root_path_unreadable",
        )

    def test_nonobject_marker_result_never_binds_a_root(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="nonobject-marker")
        monkeypatch.setattr(external_library, "_read_root_marker", lambda _root: [])

        assert external_library.root_binding_state(library) == (
            "invalid",
            "root_marker_invalid",
        )

    def test_oversized_marker_never_binds_a_root(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="oversized-marker")
        marker = root / external_library.ROOT_MARKER_FILENAME
        marker.write_bytes(b"x" * 4097)

        assert external_library.root_binding_state(library) == (
            "invalid",
            "root_marker_invalid",
        )

    @pytest.mark.parametrize(
        ("failure", "expected_state"),
        [
            pytest.param(PermissionError("denied"), "unreadable", id="permission"),
            pytest.param(OSError(errno.EIO, "transport error"), "invalid", id="io"),
            pytest.param(UnicodeError("invalid encoding"), "invalid", id="encoding"),
        ],
    )
    def test_enrollment_rejects_an_unreadable_existing_marker(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
        failure: Exception,
        expected_state: str,
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="enrollment-failure"
        )

        def fail(_root: Path) -> dict[str, object]:
            raise failure

        monkeypatch.setattr(external_library, "_read_root_marker", fail)

        with pytest.raises(external_library.ExternalRootBindingError) as caught:
            external_library.enroll_external_root(db_session, library)

        assert caught.value.state == expected_state
        assert library.root_identity is None

    def test_enrollment_never_overwrites_a_concurrent_marker(
        self,
        tmp_path: Path,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="concurrent-marker"
        )
        monkeypatch.setattr(external_library, "_create_marker", lambda *_args: False)

        with pytest.raises(external_library.ExternalRootBindingError) as caught:
            external_library.enroll_external_root(db_session, library)

        assert caught.value.state == "mismatch"
        assert library.root_identity is None

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param([], id="not-object"),
            pytest.param(
                {
                    "format": 2,
                    "installation": "a" * 64,
                    "role": external_library.ROOT_MARKER_ROLE,
                    "library_id": 1,
                    "root_identity": "b" * 64,
                },
                id="wrong-format",
            ),
            pytest.param(
                {
                    "format": external_library.ROOT_MARKER_FORMAT,
                    "installation": "short",
                    "role": external_library.ROOT_MARKER_ROLE,
                    "library_id": 1,
                    "root_identity": "b" * 64,
                },
                id="invalid-identity",
            ),
        ],
    )
    def test_marker_payload_rejects_noncanonical_identity(
        self, payload: object
    ) -> None:
        with pytest.raises(ValueError, match="root_marker_invalid"):
            external_library._validate_marker_payload(payload)  # noqa: SLF001

    def test_legacy_library_is_unbound_without_scan_mutation(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        drop_gcode(root, "new.gcode")
        library = build_external_library(
            db_session, root, root_identity=None, name="legacy"
        )

        result = external_library.scan_library(library.id)

        assert result["aborted"] is True
        assert result["error"] == "legacy_library_requires_explicit_enrollment"
        assert (
            db_session.exec(
                select(File).where(File.external_library_id == library.id, live(File))
            ).all()
            == []
        )

    def test_wrong_marker_aborts_without_reindex_or_trash(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        source = drop_gcode(root, "known.gcode")
        library = build_external_library(db_session, root, name="nas")
        first = external_library.scan_library(library.id)
        assert first["added"] == 1
        indexed = db_session.exec(
            select(File).where(File.external_library_id == library.id, live(File))
        ).all()

        marker = root / external_library.ROOT_MARKER_FILENAME
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["root_identity"] = "b" * 64
        marker.write_text(json.dumps(payload), encoding="utf-8")

        result = external_library.scan_library(library.id)

        assert result["aborted"] is True
        assert result["removed"] == 0
        assert (
            db_session.exec(
                select(File).where(File.external_library_id == library.id, live(File))
            ).all()
            == indexed
        )
        assert source.exists()

    def test_nonhex_marker_token_is_not_enrollable(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="nas")
        marker = root / external_library.ROOT_MARKER_FILENAME
        payload = json.loads(marker.read_text(encoding="utf-8"))
        payload["root_identity"] = "z" * 64
        marker.write_text(json.dumps(payload), encoding="utf-8")

        assert external_library.root_binding_state(library) == (
            "invalid",
            "root_marker_invalid",
        )

    def test_symlink_marker_is_not_enrollable(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="nas")
        marker = root / external_library.ROOT_MARKER_FILENAME
        target = tmp_path / "foreign-marker"
        target.write_bytes(marker.read_bytes())
        marker.unlink()
        marker.symlink_to(target)

        assert external_library.root_binding_state(library) == (
            "invalid",
            "root_marker_invalid",
        )

    def test_root_swap_during_walk_aborts_before_catalog_mutation(
        self, tmp_path: Path, db_session: Session, monkeypatch
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        drop_gcode(root, "new.gcode")
        library = build_external_library(db_session, root, name="nas")
        original_walk = external_library._walk

        def swap_then_walk(scan_root):  # type: ignore[no-untyped-def]
            marker = root / external_library.ROOT_MARKER_FILENAME
            payload = json.loads(marker.read_text(encoding="utf-8"))
            payload["root_identity"] = "d" * 64
            marker.write_text(json.dumps(payload), encoding="utf-8")
            return original_walk(scan_root)

        monkeypatch.setattr(external_library, "_walk", swap_then_walk)

        result = external_library.scan_library(library.id)

        assert result["aborted"] is True
        assert result["removed"] == 0
        assert (
            db_session.exec(
                select(File).where(File.external_library_id == library.id, live(File))
            ).all()
            == []
        )

    def test_markerless_root_can_be_explicitly_enrolled(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="legacy"
        )

        external_library.enroll_external_root(db_session, library)

        assert library.root_identity is not None
        assert len(library.root_identity) == 64
        state, reason = external_library.root_binding_state(library)
        assert (state, reason) == ("bound", None)
        marker = json.loads(
            (root / external_library.ROOT_MARKER_FILENAME).read_text(encoding="utf-8")
        )
        assert marker["library_id"] == library.id
        assert marker["root_identity"] == library.root_identity

    def test_matching_orphan_marker_can_be_explicitly_reenrolled(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="orphaned-enrollment"
        )
        marker = {
            "format": external_library.ROOT_MARKER_FORMAT,
            "installation": "a" * 64,
            "role": external_library.ROOT_MARKER_ROLE,
            "library_id": library.id,
            "root_identity": "c" * 64,
        }
        marker_path = root / external_library.ROOT_MARKER_FILENAME
        marker_path.write_text(json.dumps(marker), encoding="utf-8")

        external_library.enroll_external_root(db_session, library)

        assert library.root_identity == marker["root_identity"]
        assert external_library.root_binding_state(library) == ("bound", None)
        assert json.loads(marker_path.read_text(encoding="utf-8")) == marker

    def test_markerless_replacement_requires_token_rotation(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(db_session, root, name="nas")
        old_identity = library.root_identity
        (root / external_library.ROOT_MARKER_FILENAME).unlink()

        external_library.enroll_external_root(db_session, library)

        assert library.root_identity != old_identity
        assert external_library.root_binding_state(library) == ("bound", None)

    def test_remount_with_original_marker_recovers_without_reenrollment(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        drop_gcode(root, "stable.gcode")
        library = build_external_library(db_session, root, name="nas")
        assert external_library.scan_library(library.id)["added"] == 1
        original = tmp_path / "original-mount"
        root.rename(original)
        root.mkdir()

        unavailable = external_library.scan_library(library.id)
        assert unavailable["aborted"] is True
        replacement = tmp_path / "replacement"
        root.rename(replacement)
        original.rename(root)

        assert external_library.root_binding_state(library) == ("bound", None)
        recovered = external_library.scan_library(library.id)
        assert recovered["aborted"] is False
        assert recovered["skipped"] == 1

    def test_conflicting_marker_is_never_overwritten(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        marker = root / external_library.ROOT_MARKER_FILENAME
        original = {
            "format": 1,
            "installation": "a" * 64,
            "role": "external-library",
            "library_id": 999,
            "root_identity": "c" * 64,
        }
        marker.write_text(json.dumps(original), encoding="utf-8")
        library = build_external_library(
            db_session, root, root_identity=None, name="adopt"
        )

        try:
            external_library.enroll_external_root(db_session, library)
        except external_library.ExternalRootBindingError as exc:
            assert exc.state == "mismatch"
        else:  # pragma: no cover - assertion keeps the behavior explicit
            raise AssertionError("conflicting marker was adopted")

        assert json.loads(marker.read_text(encoding="utf-8")) == original
        db_session.refresh(library)
        assert library.root_identity is None

    def test_external_write_target_rejects_unbound_root(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="legacy"
        )
        model = build_model(db_session, name="new")

        try:
            resolve_write_target(
                db_session,
                model=model,
                original_filename="new.gcode",
                collection=None,
                target_library_id=library.id,
            )
        except external_library.ExternalRootBindingError as exc:
            assert exc.state == "unbound"
        else:  # pragma: no cover - assertion keeps the behavior explicit
            raise AssertionError("unbound root was used for write-back")

        assert not (root / "new.gcode").exists()

    def test_external_write_target_rejects_missing_explicit_library(
        self, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        model = build_model(db_session, name="new")

        try:
            resolve_write_target(
                db_session,
                model=model,
                original_filename="new.gcode",
                collection=None,
                target_library_id=999999,
            )
        except external_library.ExternalRootBindingError as exc:
            assert exc.state == "missing"
        else:  # pragma: no cover - assertion keeps the behavior explicit
            raise AssertionError("missing explicit library was silently ignored")

    def test_external_write_target_rejects_missing_linked_library(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        _enable_feature(db_session)
        model = build_model(db_session, name="linked")
        library = build_external_library(db_session, tmp_path / "linked", name="linked")
        build_file(
            db_session,
            model,
            filename="linked.gcode",
            external=True,
            external_library_id=library.id,
        )
        # A stale external reference can exist after an operator removes the
        # library row outside PrintStash (or during a legacy repair).  Keep the
        # file row so this exercises the linked-library lookup rather than the
        # database's FK validation on an impossible insert.
        db_session.commit()
        connection = db_session.connection()
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.exec_driver_sql(
            "DELETE FROM external_libraries WHERE id = :id", {"id": library.id}
        )
        db_session.commit()
        db_session.connection().exec_driver_sql("PRAGMA foreign_keys=ON")

        try:
            resolve_write_target(
                db_session,
                model=model,
                original_filename="revision.gcode",
                collection=None,
                target_library_id=None,
            )
        except external_library.ExternalRootBindingError as exc:
            assert exc.state == "missing"
        else:  # pragma: no cover - assertion keeps the behavior explicit
            raise AssertionError("missing linked library was silently ignored")

    def test_enrollment_commit_failure_preserves_untrusted_marker(
        self, tmp_path: Path, db_session: Session, monkeypatch
    ) -> None:
        _enable_feature(db_session)
        root = tmp_path / "nas"
        root.mkdir()
        library = build_external_library(
            db_session, root, root_identity=None, name="legacy"
        )

        def fail_commit() -> None:
            raise RuntimeError("commit outcome unknown")

        monkeypatch.setattr(db_session, "commit", fail_commit)
        try:
            external_library.enroll_external_root(db_session, library)
        except RuntimeError as exc:
            assert str(exc) == "commit outcome unknown"
        else:  # pragma: no cover - assertion keeps the behavior explicit
            raise AssertionError("enrollment unexpectedly committed")

        # The marker is retained because a commit exception has an unknown
        # outcome; without a matching durable DB row it is never trusted.
        assert (root / external_library.ROOT_MARKER_FILENAME).exists()
        assert library.root_identity is None
