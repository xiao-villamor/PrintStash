"""Call signatures for the session-bound `make_*` fixtures.

`partial(build_model, session)` is opaque to a type checker: it returns a plain
callable, so annotating a fixture with it gives a test writer no autocomplete and
pyright no way to catch a misspelled keyword. These protocols restore both. They
describe each builder **as the fixture exposes it** — the same signature minus
the leading session — which is why they live beside the builders rather than
being generated from them.

Annotate a test's parameter with the protocol whenever the extra clarity is worth
it (`make_model: MakeModel`); the fixtures work without it. Keep a protocol in
step with its builder in the same commit — a stale one is worse than none, since
it type-checks a signature that no longer exists.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from app.db.models import (
    ArtifactProvenanceLink,
    CaptureUploadSlot,
    Collection,
    CollectionPermission,
    CollectionRole,
    Document,
    DocumentKind,
    ExternalLibrary,
    File,
    FileRevisionStatus,
    FileType,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    Printer,
    PrinterFile,
    PrinterProvider,
    PrinterStatus,
    PrintJob,
    PrintJobState,
    ProvenanceCapture,
    ShareLink,
    User,
)


class MakeUser(Protocol):
    def __call__(
        self,
        username: str | None = None,
        *,
        superuser: bool = False,
        active: bool = True,
        password: str = ...,
        **overrides: Any,
    ) -> User: ...


class HeadersFor(Protocol):
    def __call__(self, user: User, *, scope: str | None = None) -> dict[str, str]: ...


class UserHeaders(Protocol):
    def __call__(
        self,
        username: str | None = None,
        *,
        is_superuser: bool = False,
        scope: str = "write",
        password: str = ...,
    ) -> dict[str, str]: ...


class GrantRole(Protocol):
    def __call__(
        self,
        user: User,
        collection: Collection,
        role: CollectionRole = ...,
    ) -> CollectionPermission: ...


class MakeModel(Protocol):
    def __call__(
        self,
        name: str = "Bracket",
        *,
        collection: Collection | None = None,
        trashed: bool | datetime = False,
        **overrides: Any,
    ) -> Model: ...


class MakeFile(Protocol):
    def __call__(
        self,
        model: Model,
        *,
        file_type: FileType | None = None,
        filename: str | None = None,
        recommended: bool = False,
        status: FileRevisionStatus | None = None,
        trashed: bool | datetime = False,
        external: bool = False,
        metadata: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> File: ...


class MakeCollection(Protocol):
    def __call__(
        self,
        name: str = "Parts",
        *,
        parent: Collection | None = None,
        **overrides: Any,
    ) -> Collection: ...


class MakePrinter(Protocol):
    def __call__(
        self,
        name: str | None = None,
        *,
        provider: PrinterProvider = ...,
        status: PrinterStatus = ...,
        trashed: bool = False,
        **overrides: Any,
    ) -> Printer: ...


class MakePrinterFile(Protocol):
    def __call__(
        self,
        printer: Printer,
        *,
        file: File | None = None,
        remote_filename: str | None = None,
        **overrides: Any,
    ) -> PrinterFile: ...


class MakePrintJob(Protocol):
    def __call__(
        self,
        file: File,
        *,
        printer: Printer | None = None,
        state: PrintJobState = ...,
        **overrides: Any,
    ) -> PrintJob: ...


class MakeProvenanceSource(Protocol):
    def __call__(
        self,
        model: Model,
        *,
        provider: str = "printables",
        source_item_id: str | None = "123456",
        canonical_url: str | None = None,
        tags: list[str] | None = None,
        **overrides: Any,
    ) -> ModelProvenanceSource: ...


class MakeCapture(Protocol):
    def __call__(
        self,
        source: ModelProvenanceSource,
        *,
        captured_at: datetime | None = None,
        snapshot: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> ProvenanceCapture: ...


class MakeArtifactLink(Protocol):
    def __call__(
        self, file: File, source: ModelProvenanceSource, **overrides: Any
    ) -> ArtifactProvenanceLink: ...


class MakeCover(Protocol):
    def __call__(
        self, source: ModelProvenanceSource, **overrides: Any
    ) -> ModelSourceCover: ...


class MakeInboxItem(Protocol):
    def __call__(
        self,
        owner: User,
        *,
        state: InboxItemState = ...,
        source_kind: InboxSourceKind = ...,
        manifest: dict[str, Any] | None = None,
        **overrides: Any,
    ) -> InboxItem: ...


class MakeCaptureSlot(Protocol):
    def __call__(
        self,
        item: InboxItem,
        *,
        role: str = "file",
        uploaded: bool = False,
        **overrides: Any,
    ) -> CaptureUploadSlot: ...


class MakeExternalLibrary(Protocol):
    def __call__(
        self,
        root: Path | str,
        *,
        name: str | None = None,
        scanning: bool = False,
        **overrides: Any,
    ) -> ExternalLibrary: ...


class MakeDocument(Protocol):
    def __call__(
        self,
        name: str = "manual",
        *,
        kind: DocumentKind = ...,
        trashed: bool = False,
        **overrides: Any,
    ) -> Document: ...


class MakeShareLink(Protocol):
    def __call__(
        self,
        model: Model,
        *,
        token: str = ...,
        expired: bool = False,
        revoked: bool = False,
        **overrides: Any,
    ) -> ShareLink: ...


class AGcodeArtifact(Protocol):
    def __call__(
        self, name: str = "Cube", *, dispatchable: bool = False, **overrides: Any
    ) -> File: ...


class APrinterWithAQueue(Protocol):
    def __call__(
        self, *, depth: int = 2, **overrides: Any
    ) -> tuple[Printer, list[File]]: ...


__all__ = [
    "AGcodeArtifact",
    "APrinterWithAQueue",
    "GrantRole",
    "HeadersFor",
    "MakeArtifactLink",
    "MakeCapture",
    "MakeCaptureSlot",
    "MakeCollection",
    "MakeCover",
    "MakeDocument",
    "MakeExternalLibrary",
    "MakeFile",
    "MakeInboxItem",
    "MakeModel",
    "MakePrintJob",
    "MakePrinter",
    "MakePrinterFile",
    "MakeProvenanceSource",
    "MakeShareLink",
    "MakeUser",
    "UserHeaders",
]
