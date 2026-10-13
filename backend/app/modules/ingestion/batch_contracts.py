"""Immutable batch inputs and explicit unit outcomes."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum

from app.db.models.types import IngestionEntryState
from app.modules.work.contracts import JobExecution


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name}_required")


def _positive(value: int, name: str) -> None:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{name}_positive")


@dataclass(frozen=True)
class JobBatch:
    job_id: str

    def __post_init__(self) -> None:
        _text(self.job_id, "job_id")


@dataclass(frozen=True)
class InboxBatch:
    item_id: int

    def __post_init__(self) -> None:
        _positive(self.item_id, "item_id")


BatchOwner = JobBatch | InboxBatch


class EntrySourceKind(StrEnum):
    LOCAL = "local"
    ARCHIVE = "archive"
    REMOTE = "remote"
    COLLECTION = "collection"


@dataclass(frozen=True)
class LocalSource:
    source_id: str

    def __post_init__(self) -> None:
        _text(self.source_id, "source_id")


@dataclass(frozen=True)
class RemoteSource:
    source_id: str

    def __post_init__(self) -> None:
        _text(self.source_id, "source_id")


@dataclass(frozen=True)
class CollectionSource:
    source_id: str

    def __post_init__(self) -> None:
        _text(self.source_id, "source_id")


@dataclass(frozen=True)
class ArchiveSource:
    source_id: str
    entry_id: str

    def __post_init__(self) -> None:
        _text(self.source_id, "source_id")
        _text(self.entry_id, "entry_id")


SourceDescriptor = LocalSource | RemoteSource | CollectionSource | ArchiveSource


def encode_source_descriptor(source: SourceDescriptor) -> str:
    kinds = {
        LocalSource: EntrySourceKind.LOCAL,
        RemoteSource: EntrySourceKind.REMOTE,
        CollectionSource: EntrySourceKind.COLLECTION,
        ArchiveSource: EntrySourceKind.ARCHIVE,
    }
    kind = kinds[type(source)]
    value = {"version": 1, "kind": kind.value, "source_id": source.source_id}
    if isinstance(source, ArchiveSource):
        value["entry_id"] = source.entry_id
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def decode_source_descriptor(raw: str) -> SourceDescriptor:
    value = json.loads(raw)
    if (
        not isinstance(value, dict)
        or type(value.get("version")) is not int
        or value["version"] != 1
    ):
        raise ValueError("invalid_entry_descriptor_version")
    kind = EntrySourceKind(value.get("kind"))
    expected = (
        {"version", "kind", "source_id", "entry_id"}
        if kind is EntrySourceKind.ARCHIVE
        else {"version", "kind", "source_id"}
    )
    if set(value) != expected:
        raise ValueError("invalid_entry_descriptor")
    if kind is EntrySourceKind.ARCHIVE:
        return ArchiveSource(value["source_id"], value["entry_id"])
    factories = {
        EntrySourceKind.LOCAL: LocalSource,
        EntrySourceKind.REMOTE: RemoteSource,
        EntrySourceKind.COLLECTION: CollectionSource,
    }
    return factories[kind](value["source_id"])


@dataclass(frozen=True)
class LegacyCandidate:
    """Immutable evidence for an old indexed-key Artifact within one Job."""

    ingestion_key: str
    original_filename: str
    sha256: str

    def __post_init__(self) -> None:
        _text(self.ingestion_key, "ingestion_key")
        _text(self.original_filename, "original_filename")
        if (
            not isinstance(self.sha256, str)
            or len(self.sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.sha256)
        ):
            raise ValueError("invalid_legacy_sha256")


@dataclass(frozen=True)
class EntrySpec:
    identity: str
    display_name: str
    descriptor: SourceDescriptor
    size_bytes: int | None

    def __post_init__(self) -> None:
        _text(self.identity, "identity")
        _text(self.display_name, "display_name")
        if len(self.display_name) > 512:
            raise ValueError("display_name_too_long")
        if not isinstance(self.descriptor, SourceDescriptor):
            raise TypeError("source_descriptor_required")
        if self.size_bytes is not None and (
            type(self.size_bytes) is not int or self.size_bytes < 0
        ):
            raise ValueError("invalid_entry_size")

    @property
    def key(self) -> str:
        return hashlib.sha256(
            self.identity.encode("utf-8", "surrogatepass")
        ).hexdigest()


def ingestion_key(owner: BatchOwner, key: str) -> str:
    namespace = (
        f"job:{owner.job_id}"
        if isinstance(owner, JobBatch)
        else f"inbox:{owner.item_id}"
    )
    return hashlib.sha256(f"{namespace}\0{key}".encode()).hexdigest()


@dataclass(frozen=True)
class Imported:
    model_id: int
    file_id: int

    def __post_init__(self) -> None:
        _positive(self.model_id, "model_id")
        _positive(self.file_id, "file_id")


@dataclass(frozen=True)
class Deduplicated:
    model_id: int
    file_id: int

    def __post_init__(self) -> None:
        _positive(self.model_id, "model_id")
        _positive(self.file_id, "file_id")


@dataclass(frozen=True)
class Failed:
    error_code: str
    retryable: bool

    def __post_init__(self) -> None:
        _text(self.error_code, "error_code")
        if len(self.error_code) > 128 or type(self.retryable) is not bool:
            raise ValueError("invalid_entry_failure")


@dataclass(frozen=True)
class Skipped:
    reason: str

    def __post_init__(self) -> None:
        _text(self.reason, "reason")
        if len(self.reason) > 128:
            raise ValueError("invalid_entry_skip")


EntryOutcome = Imported | Deduplicated | Failed | Skipped


@dataclass(frozen=True)
class EntryRecord:
    id: int
    key: str
    ingestion_key: str
    spec: EntrySpec
    ordinal: int
    state: IngestionEntryState
    model_id: int | None
    file_id: int | None
    error_code: str | None
    retryable: bool

    @property
    def published(self) -> bool:
        return self.state in (
            IngestionEntryState.IMPORTED,
            IngestionEntryState.DEDUPLICATED,
        )


@dataclass(frozen=True)
class BatchCounts:
    total: int
    pending: int
    imported: int
    deduplicated: int
    failed: int
    skipped: int

    @property
    def processed(self) -> int:
        return self.total - self.pending

    @property
    def succeeded(self) -> int:
        return self.imported + self.deduplicated


@dataclass(frozen=True)
class BatchCommitReference:
    owner: BatchOwner
    entry_id: int
    execution: JobExecution

    def __post_init__(self) -> None:
        _positive(self.entry_id, "entry_id")
