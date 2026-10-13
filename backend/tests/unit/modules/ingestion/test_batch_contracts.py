"""Batch identity and versioned source snapshots are independent of retries."""

import json

import pytest

from app.modules.ingestion.batch_contracts import (
    ArchiveSource,
    CollectionSource,
    EntrySpec,
    InboxBatch,
    JobBatch,
    LocalSource,
    RemoteSource,
    decode_source_descriptor,
    encode_source_descriptor,
    ingestion_key,
)


class TestSourceSnapshot:
    @pytest.mark.parametrize(
        "source",
        [
            LocalSource("local"),
            RemoteSource("provider/file"),
            CollectionSource("member"),
            ArchiveSource("zip-sha", "2:abcd:14"),
        ],
    )
    def test_preserves_the_typed_source_identity(self, source):
        assert decode_source_descriptor(encode_source_descriptor(source)) == source

    @pytest.mark.parametrize(
        "invalid",
        [
            {"version": 2, "kind": "local", "source_id": "x"},
            {"version": True, "kind": "local", "source_id": "x"},
            {"version": 1, "kind": "unknown", "source_id": "x"},
            {"version": 1, "kind": "local", "source_id": "x", "entry_id": "unexpected"},
            {"version": 1, "kind": "archive", "source_id": "x"},
            {"version": 1, "kind": "archive", "source_id": "x", "entry_id": ""},
        ],
    )
    def test_refuses_invalid_source_snapshots(self, invalid):
        with pytest.raises(ValueError):
            decode_source_descriptor(json.dumps(invalid))

    @pytest.mark.parametrize("size", [-1, True, 1.5])
    def test_refuses_invalid_byte_sizes(self, size):
        with pytest.raises(ValueError, match="invalid_entry_size"):
            EntrySpec("identity", "part.stl", LocalSource("source"), size)


class TestEntryIdentity:
    def test_separates_durable_owner_namespaces(self):
        spec = EntrySpec("selected-id/entry", "part.stl", LocalSource("source"), None)
        assert ingestion_key(InboxBatch(12), spec.key) != ingestion_key(
            JobBatch("retry-job"), spec.key
        )
        assert ingestion_key(InboxBatch(12), spec.key) != ingestion_key(
            InboxBatch(13), spec.key
        )
        assert len(ingestion_key(InboxBatch(12), spec.key)) == 64
