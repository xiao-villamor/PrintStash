"""Refused writes never reserve a locator; uncertain writes retain evidence."""

from functools import partial
from io import BytesIO
from pathlib import Path

import pytest
from sqlmodel import select

from app.db.models import OwnedStorageObject, StorageObjectState
from app.modules.storage.storage_backend.contracts import StorageConfigurationError
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_ownership import (
    prepare_bytes,
    prepare_file,
    prepare_stream,
)


@pytest.fixture(params=["bytes", "stream", "file", "move"], ids=str)
def publication(request, db_session, local_storage, tmp_path):
    backend = get_backend()
    key = backend.thumbnail_key(123)
    source = tmp_path / "source.bin"
    source.write_bytes(b"thumbnail")
    calls = {
        "bytes": partial(prepare_bytes, db_session, backend, key, b"thumbnail"),
        "stream": partial(
            prepare_stream, db_session, backend, key, BytesIO(b"thumbnail")
        ),
        "file": partial(prepare_file, db_session, backend, key, source),
        "move": partial(prepare_file, db_session, backend, key, source, move=True),
    }
    return partial(calls[request.param], object_kind="thumbnail")


class TestPreparePublication:
    def test_refused_write_leaves_no_reservation(
        self, publication, db_session, local_storage
    ):
        backend = get_backend()
        (local_storage / "thumbs" / ".printstash-storage-root.json").unlink()
        backend.ensure_setup()
        with pytest.raises(StorageConfigurationError, match="storage_write_unverified"):
            publication()
        assert db_session.exec(select(OwnedStorageObject)).all() == []

    def test_uncertain_write_retains_recovery_evidence(
        self, db_session, local_storage, monkeypatch
    ):
        backend = get_backend()
        key = backend.thumbnail_key(124)
        create = backend.create_bytes

        def lost_receipt(data, destination):
            create(data, destination)
            raise OSError("receipt lost after publication")

        monkeypatch.setattr(backend, "create_bytes", lost_receipt)
        with pytest.raises(OSError, match="receipt lost"):
            prepare_bytes(
                db_session, backend, key, b"thumbnail", object_kind="thumbnail"
            )
        row = db_session.exec(select(OwnedStorageObject)).one()
        assert row.state is StorageObjectState.PENDING
        assert row.last_error == "OSError"
        assert Path(key).read_bytes() == b"thumbnail"
