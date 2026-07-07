"""Corner cases at the seam between external (NAS) libraries and the trash
lifecycle — the places where the "NAS bytes are sacred" and dedup invariants
interact and a mistake would mean silent data loss.

These complement ``test_external_libraries.py`` (focused unit tests) and
``test_external_libraries_integration.py`` (workflows) by pinning down the
*mixed model* and *duplicate-content* boundaries those don't cover:

* A Model can own BOTH a vault-owned file and a NAS-linked file (dedup unifies a
  web upload and an identical file discovered on a NAS). Removing the library, or
  hard-deleting, must respect each blob's ownership independently.
* Two NAS files with identical content dedup into one Model with two linked
  files; reconciliation must track them per-path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import _overlay
from app.db.models import ExternalLibrary, File, Model
from app.db.scopes import live
from app.services import external_library, runtime_config, trash
from app.services.ingestion import ingest_orca_gcode
from app.services.storage_backend import get_backend

FIXTURE_GCODE = Path(__file__).parent / "fixtures" / "sample.gcode"


def _configure_storage(tmp_path: Path) -> None:
    _overlay["data_dir"] = tmp_path / "files"
    _overlay["thumb_dir"] = tmp_path / "thumbs"
    _overlay["staging_dir"] = tmp_path / "staging"
    (tmp_path / "files").mkdir(parents=True, exist_ok=True)
    (tmp_path / "thumbs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "staging" / "_incoming").mkdir(parents=True, exist_ok=True)


def _enable_feature(session: Session) -> None:
    runtime_config.set_external_libraries_enabled(session, True)


def _make_library(session: Session, root: Path, **kw) -> ExternalLibrary:
    lib = ExternalLibrary(name="nas", root_path=str(root), **kw)
    session.add(lib)
    session.commit()
    session.refresh(lib)
    return lib


def _drop(dest_dir: Path, name: str, data: bytes) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / name
    target.write_bytes(data)
    return target


def _ingest_vault_gcode(name: str, data: bytes) -> None:
    """Ingest a g-code into vault storage (no target library)."""
    staged = Path(_overlay["staging_dir"]) / "_incoming" / f"{uuid.uuid4().hex}.gcode"
    staged.write_bytes(data)
    ingest_orca_gcode(
        job_id=f"job-{uuid.uuid4().hex[:8]}",
        staged_path=staged,
        original_filename=name,
        model_name=Path(name).stem,
        collection=None,
        tags=None,
        source_hash=None,
        target_library_id=None,
    )


def _make_mixed_model(
    tmp_path: Path, db_session: Session
) -> tuple[ExternalLibrary, Model, File, File]:
    """Build a Model that owns a vault file AND a NAS-linked file via dedup.

    A web upload lands in vault; the identical bytes already sitting on the NAS
    are then discovered by a scan and, because the content hash matches, attach
    as a second (external) File on the very same Model.
    """
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    payload = FIXTURE_GCODE.read_bytes()

    # 1) Vault upload.
    _ingest_vault_gcode("shared.gcode", payload)

    # 2) Identical bytes on the NAS, then a scan → dedup attaches it externally.
    nas = tmp_path / "nas"
    _drop(nas, "shared.gcode", payload)
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)

    db_session.expire_all()
    files = db_session.exec(select(File).where(live(File))).all()
    files = [f for f in files if f.original_filename == "shared.gcode"]
    assert len(files) == 2, "expected one vault + one external file on one model"
    vault_file = next(f for f in files if not f.is_external)
    ext_file = next(f for f in files if f.is_external)
    assert vault_file.model_id == ext_file.model_id, "dedup must unify the model"
    model = db_session.get(Model, vault_file.model_id)
    return lib, model, vault_file, ext_file


# --------------------------------------------------------------------------- #
# Mixed vault + external model
# --------------------------------------------------------------------------- #
def test_purge_library_index_keeps_model_with_vault_files(
    tmp_path: Path, db_session: Session
) -> None:
    """Removing a NAS library trashes its linked files, but a Model that still
    owns a vault file must stay live — only the external File is soft-deleted."""
    lib, model, vault_file, ext_file = _make_mixed_model(tmp_path, db_session)

    trashed = external_library.purge_library_index(db_session, lib.id)

    assert trashed == 1
    db_session.expire_all()
    # The model survives because it still has a vault-owned file.
    assert db_session.get(Model, model.id).deleted_at is None
    assert db_session.get(File, vault_file.id).deleted_at is None
    # The external link is trashed (the NAS bytes themselves are untouched).
    assert db_session.get(File, ext_file.id).deleted_at is not None
    assert Path(ext_file.path).exists()


def test_hard_delete_mixed_model_deletes_vault_blob_keeps_nas_bytes(
    tmp_path: Path, db_session: Session
) -> None:
    """Hard-deleting a model with both file kinds removes the vault blob but must
    never touch the NAS-linked bytes; both DB rows go."""
    _lib, model, vault_file, ext_file = _make_mixed_model(tmp_path, db_session)
    backend = get_backend()
    assert backend.exists(vault_file.path)
    nas_path = ext_file.path
    nas_bytes = Path(nas_path).read_bytes()

    trash.soft_delete_model(db_session, model)
    trash.hard_delete_model(db_session, model)
    db_session.commit()

    db_session.expire_all()
    assert db_session.get(Model, model.id) is None
    assert db_session.get(File, vault_file.id) is None
    assert db_session.get(File, ext_file.id) is None
    # Vault blob purged...
    assert not backend.exists(vault_file.path)
    # ...NAS bytes preserved, byte-for-byte.
    assert Path(nas_path).exists()
    assert Path(nas_path).read_bytes() == nas_bytes


def test_hard_delete_tagged_model_succeeds_and_keeps_the_tag(
    db_session: Session,
) -> None:
    """Regression: purging a *tagged* model must not raise ``StaleDataError``.

    ``Model.tags`` is a ``link_model`` (many-to-many) relationship, so deleting
    the model already removes its ``ModelTagLink`` rows. A manual bulk-delete of
    those rows used to run *as well*, so the ORM's cascade then tried to delete
    rows that were already gone -> ``StaleDataError`` on commit. That 500'd the
    purge endpoint and the expired-trash cron for any model carrying a tag.
    """
    from app.db.models import ModelTagLink, Tag

    uid = uuid.uuid4().hex
    model = Model(name=f"Tagged {uid[:6]}", slug=f"tagged-{uid[:8]}", hash=(uid * 2)[:64])
    tag = Tag(name=f"tag-{uid[:6]}", slug=f"tag-{uid[:6]}")
    db_session.add(model)
    db_session.add(tag)
    db_session.commit()
    db_session.refresh(model)
    db_session.refresh(tag)
    db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
    db_session.commit()

    trash.soft_delete_model(db_session, model)
    trash.hard_delete_model(db_session, model)
    db_session.commit()  # must not raise StaleDataError

    db_session.expire_all()
    assert db_session.get(Model, model.id) is None
    # The shared tag survives; only the association row is gone.
    assert db_session.get(Tag, tag.id) is not None
    assert (
        db_session.exec(
            select(ModelTagLink).where(ModelTagLink.model_id == model.id)
        ).first()
        is None
    )


# --------------------------------------------------------------------------- #
# Duplicate content across two NAS files
# --------------------------------------------------------------------------- #
def test_scan_dedups_identical_nas_files_into_one_model(
    tmp_path: Path, db_session: Session
) -> None:
    """Two NAS files with identical content index as two linked Files under a
    single deduplicated Model — not two models, not one dropped file."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    payload = FIXTURE_GCODE.read_bytes()
    nas = tmp_path / "nas"
    _drop(nas, "a.gcode", payload)
    _drop(nas, "b.gcode", payload)
    lib = _make_library(db_session, nas)

    summary = external_library.scan_library(lib.id)

    assert summary["added"] == 2
    assert summary["aborted"] is False
    db_session.expire_all()
    files = db_session.exec(
        select(File).where(File.is_external == True, live(File))  # noqa: E712
    ).all()
    assert len(files) == 2
    assert len({f.model_id for f in files}) == 1, "identical content → one model"
    # Exactly one revision carries the recommended marker.
    assert sum(1 for f in files if f.is_recommended) == 1


def test_scan_removing_one_duplicate_keeps_model_until_all_gone(
    tmp_path: Path, db_session: Session
) -> None:
    """When duplicate-content files share a model, removing one from disk trashes
    only that File; the model is trashed only once every linked File is gone."""
    _configure_storage(tmp_path)
    _enable_feature(db_session)
    payload = FIXTURE_GCODE.read_bytes()
    nas = tmp_path / "nas"
    a = _drop(nas, "a.gcode", payload)
    b = _drop(nas, "b.gcode", payload)
    lib = _make_library(db_session, nas)
    external_library.scan_library(lib.id)
    db_session.expire_all()
    model_id = (
        db_session.exec(
            select(File).where(File.is_external == True)  # noqa: E712
        )
        .first()
        .model_id
    )

    # Remove one of the two duplicates.
    a.unlink()
    summary = external_library.scan_library(lib.id)
    assert summary["removed"] == 1
    db_session.expire_all()
    assert db_session.get(Model, model_id).deleted_at is None, "still has b.gcode"

    # Remove the last one → the model itself is trashed.
    b.unlink()
    # Keep the root non-empty so the safety guard doesn't abort the scan.
    _drop(nas, "unrelated.gcode", payload + b"\n; distinct\nG1 X9 Y9\n")
    summary = external_library.scan_library(lib.id)
    db_session.expire_all()
    assert db_session.get(Model, model_id).deleted_at is not None


# --------------------------------------------------------------------------- #
# trash_expires_at pure helper
# --------------------------------------------------------------------------- #
def test_trash_expires_at_adds_retention_window() -> None:
    deleted = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert trash.trash_expires_at(deleted, 30) == deleted + timedelta(days=30)


def test_trash_expires_at_zero_retention_is_immediate() -> None:
    deleted = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    assert trash.trash_expires_at(deleted, 0) == deleted


def test_trash_expires_at_disabled_or_live_returns_none() -> None:
    deleted = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    # Negative retention = never auto-purge → no expiry.
    assert trash.trash_expires_at(deleted, -1) is None
    # A live row (no deleted_at) has no expiry.
    assert trash.trash_expires_at(None, 30) is None
