"""Remote byte I/O shared by Library sources and backup replicas.

A publication receipt records observations, not an atomic-create guarantee.
Managed creation and identity-bound deletion are separate optional extensions.
Readers and listings are context managed: abandoning a consumer releases its
transport handles. Keys include the existing namespace; identities are unchanged.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Iterator, Protocol

from app.services.storage_backend import CreationReceipt, StorageObjectInfo
from app.services.storage_identity import StorageTargetIdentity


@dataclass(frozen=True)
class RemoteEntry:
    key: str
    size: int
    is_dir: bool
    modified_at: datetime | None = None
    etag: str | None = None
    version_id: str | None = None


@dataclass(frozen=True)
class RemoteCapabilities:
    read: bool
    write: bool
    list: bool
    conditional_create: bool
    atomic_visibility: bool
    versioned_delete: bool


class ManagedCreation(Protocol):
    """Native create-only publication; a losing writer never replaces the winner.

    atomic_visibility separately describes whether readers can observe partial
    bytes while the winning publisher writes (SFTP exclusive creation can).
    """

    @property
    def atomic_visibility(self) -> bool: ...

    def create_stream(self, source: BinaryIO, key: str) -> CreationReceipt: ...


class IdentityDeletion(Protocol):
    """Delete one immutable version, never the current object by path alone."""

    def delete_versioned(self, key: str, version_id: str) -> None: ...


class RemoteIO(Protocol):
    backend_name: str

    @property
    def storage_target(self) -> StorageTargetIdentity | None: ...

    @property
    def source_namespace(self) -> str: ...

    @property
    def operations(self) -> RemoteCapabilities: ...

    @property
    def managed_creation(self) -> ManagedCreation | None: ...

    @property
    def exact_deletion(self) -> IdentityDeletion | None: ...

    def source_key(self, relative: str) -> str: ...
    def source_relative_key(self, key: str) -> str: ...
    def namespace_for(self, key: str) -> str: ...
    def exists(self, key: str) -> bool: ...
    def check(self) -> None: ...
    def object_info(self, key: str) -> StorageObjectInfo | None: ...
    def open_reader(
        self, key: str, *, expected: StorageObjectInfo | None = None
    ) -> AbstractContextManager[BinaryIO]: ...
    def iter_directory(
        self, relative: str
    ) -> AbstractContextManager[Iterator[RemoteEntry]]: ...
    def publish_replica(self, source: BinaryIO, key: str) -> CreationReceipt: ...
