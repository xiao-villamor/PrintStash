from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import ArtifactUploadSession
from app.modules.ingestion.artifact_uploads.api_chunks import (
    ApiChunkError,
    ApiChunkUploadAdapter,
)
from app.modules.ingestion.artifact_uploads.contracts import ChunkReceipt


def _session(payload: bytes) -> ArtifactUploadSession:
    return ArtifactUploadSession(
        id="unit-upload",
        owner_user_id=1,
        purpose="model",
        target_role="new_model",
        filename="part.stl",
        media_type="model/stl",
        declared_size=len(payload),
        client_sha256=hashlib.sha256(payload).hexdigest(),
        adapter_id="api_chunks",
        expires_at=utcnow() + timedelta(hours=1),
    )


def _receipt(index: int, offset: int, payload: bytes) -> ChunkReceipt:
    return ChunkReceipt(
        index=index,
        offset=offset,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_writes_fixed_chunks_idempotently_and_assembles_exact_bytes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.modules.ingestion.artifact_uploads.api_chunks.CHUNK_SIZE", 4
    )
    payload = b"abcdefg"
    session = _session(payload)
    adapter = ApiChunkUploadAdapter(tmp_path)
    adapter.session_directory(session.id, create=True)

    adapter.write_chunk(session, _receipt(0, 0, b"abcd"), b"abcd")
    adapter.write_chunk(session, _receipt(0, 0, b"abcd"), b"abcd")
    adapter.write_chunk(session, _receipt(1, 4, b"efg"), b"efg")
    verified = adapter.assemble(session)

    assert verified.materialize().read_bytes() == payload
    assert verified.size_bytes == len(payload)
    assert verified.sha256 == hashlib.sha256(payload).hexdigest()


@pytest.mark.parametrize(
    ("receipt", "payload", "code"),
    [
        (_receipt(-1, 0, b"abcd"), b"abcd", "chunk_index_invalid"),
        (_receipt(0, 1, b"abcd"), b"abcd", "chunk_offset_invalid"),
        (_receipt(0, 0, b"abc"), b"abc", "chunk_length_invalid"),
        (_receipt(0, 0, b"abcd"), b"abce", "chunk_hash_invalid"),
    ],
)
def test_rejects_invalid_chunk_envelopes(
    tmp_path, monkeypatch, receipt: ChunkReceipt, payload: bytes, code: str
) -> None:
    monkeypatch.setattr(
        "app.modules.ingestion.artifact_uploads.api_chunks.CHUNK_SIZE", 4
    )
    session = _session(b"abcdefg")
    adapter = ApiChunkUploadAdapter(tmp_path)
    adapter.session_directory(session.id, create=True)

    with pytest.raises(ApiChunkError, match=code):
        adapter.write_chunk(session, receipt, payload)


def test_conflicting_duplicate_preserves_the_first_chunk(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.ingestion.artifact_uploads.api_chunks.CHUNK_SIZE", 4
    )
    session = _session(b"abcd")
    adapter = ApiChunkUploadAdapter(tmp_path)
    directory = adapter.session_directory(session.id, create=True)
    adapter.write_chunk(session, _receipt(0, 0, b"abcd"), b"abcd")

    with pytest.raises(ApiChunkError, match="chunk_conflict"):
        adapter.write_chunk(session, _receipt(0, 0, b"abce"), b"abce")

    assert (directory / "00000000.chunk").read_bytes() == b"abcd"


def test_incomplete_assembly_is_never_published(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "app.modules.ingestion.artifact_uploads.api_chunks.CHUNK_SIZE", 4
    )
    session = _session(b"abcdefg")
    adapter = ApiChunkUploadAdapter(tmp_path)
    directory = adapter.session_directory(session.id, create=True)
    adapter.write_chunk(session, _receipt(0, 0, b"abcd"), b"abcd")

    with pytest.raises(ApiChunkError, match="artifact_upload_incomplete"):
        adapter.assemble(session)

    assert not (directory / "assembled.upload").exists()


def test_verified_identity_rejects_replacement(tmp_path) -> None:
    payload = b"verified"
    session = _session(payload)
    adapter = ApiChunkUploadAdapter(tmp_path)
    adapter.session_directory(session.id, create=True)
    adapter.write_chunk(session, _receipt(0, 0, payload), payload)
    verified = adapter.assemble(session)
    verified.path.unlink()
    verified.path.write_bytes(b"replaced")

    with pytest.raises(RuntimeError, match="identity_changed"):
        verified.materialize()


def test_abort_removes_only_known_owned_names(tmp_path) -> None:
    payload = b"owned"
    session = _session(payload)
    adapter = ApiChunkUploadAdapter(tmp_path)
    directory = adapter.session_directory(session.id, create=True)
    adapter.write_chunk(session, _receipt(0, 0, payload), payload)
    foreign = directory / "not-an-upload-file"
    foreign.write_bytes(b"preserve")

    with pytest.raises(OSError):
        adapter.abort_owned(session)

    assert foreign.read_bytes() == b"preserve"
