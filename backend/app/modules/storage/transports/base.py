"""The contract every storage transport declares once.

A transport is the byte protocol behind a provider (local filesystem, S3,
WebDAV, SFTP, an OpenDAL service). Everything that differs between transports
lives on its ``Transport`` descriptor: how a typed configuration resolves to a
``TransportSpec``, which runtime it needs, how it publishes create-only, how it
lists, and which durable identity it has. Adapters, the catalogue, the
connection boundary and the receipts read these declarations; none of them
branches on a transport's name.

This module imports no optional dependency and no application settings, so the
image build can load the registry before configuration exists.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, ClassVar, Generic, Literal, TypeVar
from urllib.parse import urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.storage.target_identity import StorageTargetIdentity
from app.modules.storage.transports.presets import PRESETS


class TransportKind(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    WEBDAV = "webdav"
    SFTP = "sftp"
    GDRIVE = "gdrive"


class StorageUse(str, Enum):
    """The product roles a provider can serve."""

    VAULT = "vault"
    LIBRARY = "library"
    BACKUP = "backup"


class ProviderCategory(str, Enum):
    THIS_MACHINE = "this_machine"
    S3_COMPATIBLE = "s3_compatible"
    WEBDAV = "nextcloud_webdav"
    SFTP = "nas_sftp"
    CONSUMER_CLOUD = "consumer_cloud"


class TransportSpec(BaseModel):
    kind: TransportKind
    provider: str
    namespace: str
    options: dict[str, str | int | bool]


# --- Runtimes -----------------------------------------------------------------


@dataclass(frozen=True)
class BuiltinRuntime:
    """Ships in every image; no optional dependency."""


@dataclass(frozen=True)
class AsyncSSHRuntime:
    """The full image's AsyncSSH dependency."""

    module: ClassVar[str] = "asyncssh"


@dataclass(frozen=True)
class OpenDALService:
    """One Apache OpenDAL service compiled into the full image's wheel.

    ``probe_options`` construct an operator without endpoint IO, so building one
    proves only that the service was compiled. ``cargo_feature`` is the feature
    the image build must enable; tests keep the Dockerfile in step.
    """

    scheme: str
    label: str
    probe_options: Mapping[str, str]
    feature: str | None = None

    @property
    def cargo_feature(self) -> str:
        return self.feature if self.feature is not None else f"services-{self.scheme}"


Runtime = BuiltinRuntime | AsyncSSHRuntime | OpenDALService


# --- Byte-protocol strategies -------------------------------------------------


class CreateStrategy(str, Enum):
    """How a transport publishes an object without replacing a winner."""

    # OpenDAL ``if_not_exists`` when the service advertises it, otherwise a
    # checked write (an existence check, then a streamed write).
    CONDITIONAL_WRITE = "conditional_write"
    # Stage to a temporary key, then WebDAV ``MOVE`` with ``Overwrite: F``.
    WEBDAV_MOVE = "webdav_move"
    # AsyncSSH exclusive ``x`` open (O_EXCL on the server).
    EXCLUSIVE_OPEN = "exclusive_open"
    # A one-shot writer: existence check, then one contiguous buffer. Never a
    # create-only guarantee.
    CHECKED_ONE_SHOT = "checked_one_shot"


class ListingStrategy(str, Enum):
    OPENDAL = "opendal"
    WEBDAV_PROPFIND = "webdav_propfind"


# --- Provider presentation ----------------------------------------------------


@dataclass(frozen=True)
class ProviderPresentation:
    """A catalogue entry for one provider of a transport."""

    id: str
    label: str
    category: ProviderCategory
    description: str
    expected_tier: Literal["verified", "guarded", "unguarded"]
    expected_tier_note: str
    consequences: tuple[str, ...]
    support_level: Literal["stable", "beta"]

    def as_catalogue_entry(self) -> dict[str, object]:
        return {
            "id": self.id,
            "label": self.label,
            "category": self.category.value,
            "description": self.description,
            "expected_tier": self.expected_tier,
            "expected_tier_note": self.expected_tier_note,
            "consequences": list(self.consequences),
            "documentation_url": f"/docs/storage-providers.md#{self.id}",
            "support_level": self.support_level,
        }


# --- Typed configuration ------------------------------------------------------


def config_field(
    label,
    help_text,
    *,
    default: Any = ...,
    secret=False,
    input_type="text",
    visible_for=(),
    required_for=(),
    **constraints,
):
    return Field(
        default=default,
        title=label,
        description=help_text,
        json_schema_extra={
            "secret": secret,
            "input_type": "password" if secret else input_type,
            "visible_for": list(visible_for),
            "required_for": list(required_for),
        },
        **constraints,
    )


def normalize_root(value: str) -> str:
    root = value.strip().strip("/")
    if not root or root in {".", ".."}:
        raise ValueError("storage_root_required")
    path = PurePosixPath(root)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("storage_root_traversal_forbidden")
    return path.as_posix()


class ProviderConfig(BaseModel):
    """Fields shared by every provider; each transport adds a ``provider`` literal."""

    model_config = ConfigDict(extra="forbid")

    root: str = config_field(
        "Root", "Dedicated folder or prefix", default="vault-data", input_type="path"
    )

    @model_validator(mode="before")
    @classmethod
    def preset_defaults(cls, value):
        if isinstance(value, dict):
            preset = PRESETS.get(value.get("provider", ""), {})
            return {**preset.get("defaults", {}), **value}
        return value

    @model_validator(mode="after")
    def validate_root(self):
        self.root = normalize_root(self.root)
        return self


def validate_endpoint(endpoint: str) -> None:
    parts = urlsplit(endpoint.strip())
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
    ):
        raise ValueError("provider_endpoint_invalid")


def endpoint_with_path(endpoint: str, path: str) -> str:
    validate_endpoint(endpoint)
    parts = urlsplit(endpoint.strip())
    base = urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/", "", "")
    )
    return urljoin(base, path.lstrip("/"))


# --- The descriptor -----------------------------------------------------------


@dataclass(frozen=True)
class CatalogueRequirement:
    """A cross-field rule the generated form enforces before submission."""

    kind: Literal["exactly_one", "requires", "not_value"]
    fields: tuple[str, ...]
    message: str
    value: str | None = None

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "kind": self.kind,
            "fields": list(self.fields),
        }
        if self.value is not None:
            payload["value"] = self.value
        payload["message"] = self.message
        return payload


@dataclass(frozen=True)
class FormDefaults:
    """Per-provider form defaults that differ from the config model's default."""

    values: Mapping[str, Mapping[str, str | int]] = field(default_factory=dict)

    def get(self, provider: str, name: str) -> str | int | None:
        """The override for ``name``, or None when the model default applies."""
        return self.values.get(provider, {}).get(name)


ConfigT = TypeVar("ConfigT", bound=ProviderConfig)


class Transport(ABC, Generic[ConfigT]):
    """Everything PrintStash knows about one byte protocol.

    Subclasses set the class attributes and implement ``resolve`` and
    ``target``. A transport whose bytes are reached over a network is a
    ``RemoteTransport``.
    """

    kind: ClassVar[TransportKind]
    config_model: type[ConfigT]
    # The provider a stored connection row resolves to when it names none.
    default_provider: ClassVar[str]
    providers: ClassVar[tuple[ProviderPresentation, ...]]
    # How presets of this transport describe themselves in the catalogue.
    preset_category: ClassVar[ProviderCategory]
    preset_description: ClassVar[str]
    # The runtime managed Vault storage uses; None when the transport cannot
    # hold PrintStash-owned Vault objects.
    vault_runtime: ClassVar[Runtime | None]
    # The runtime Library sources and backup replicas use.
    remote_runtime: ClassVar[Runtime]
    large_object_note: ClassVar[str]
    requirements: ClassVar[Mapping[str, tuple[CatalogueRequirement, ...]]] = {}
    form_defaults: ClassVar[FormDefaults] = FormDefaults()
    signed_get: ClassVar[bool] = False
    # The first-run setup may create a missing root.
    provisions_root: ClassVar[bool] = False
    # Credentials, not configuration, select the account: changing secrets can
    # point an existing connection at different bytes.
    secrets_select_account: ClassVar[bool] = False

    def resolve_config(self, config: ProviderConfig) -> TransportSpec:
        if not isinstance(config, self.config_model):
            raise TypeError(
                f"{type(config).__name__} is not a {self.kind.value} config"
            )
        return self.resolve(config)

    @abstractmethod
    def resolve(self, config: ConfigT) -> TransportSpec:
        """Validate cross-field rules and resolve the transport options."""

    @abstractmethod
    def target(self, options: Mapping[str, object]) -> StorageTargetIdentity | None:
        """The failure-domain identity of the configured destination."""

    def provider_domain(self, identity: StorageTargetIdentity) -> str | None:
        """A provider-wide failure domain shared by every target it hosts."""
        del identity
        return None

    @property
    def supported_uses(self) -> frozenset[StorageUse]:
        uses = {StorageUse.LIBRARY, StorageUse.BACKUP}
        if self.vault_runtime is not None:
            uses.add(StorageUse.VAULT)
        return frozenset(uses)

    def runtime_for(self, use: StorageUse) -> Runtime | None:
        if use is StorageUse.VAULT:
            return self.vault_runtime
        return self.remote_runtime


class RemoteTransport(Transport[ConfigT]):
    """A transport reached over a network through ``RemoteIO``."""

    remote_runtime: ClassVar[OpenDALService | AsyncSSHRuntime]
    create_strategy: ClassVar[CreateStrategy]
    listing: ClassVar[ListingStrategy] = ListingStrategy.OPENDAL
    # Readers cannot observe partial bytes while a conditional publisher writes.
    atomic_publication: ClassVar[bool] = False
    # HEAD answers in whole seconds while listings carry fractions.
    listed_mtime_whole_seconds: ClassVar[bool] = False

    def operator_options(self, spec: TransportSpec) -> dict[str, str]:
        """OpenDAL operator options; every OpenDAL transport implements it."""
        del spec
        raise NotImplementedError(f"{self.kind.value} is not an OpenDAL transport")

    @abstractmethod
    def provider_ref_fields(
        self,
        *,
        options: Mapping[str, object],
        backend: object,
        namespace: str,
    ) -> dict[str, object]:
        """Credential-free locator fields hashed into a durable provider ref."""

    @property
    def opendal_service(self) -> OpenDALService | None:
        runtime = self.remote_runtime
        return runtime if isinstance(runtime, OpenDALService) else None

    def conditional_create(self, write_with_if_not_exists: bool) -> bool:
        """Whether publication is create-only, given the operator's capability.

        WebDAV's ``MOVE`` is a protocol primitive the binding does not report;
        a one-shot writer is never create-only whatever the binding reports.
        """
        if self.create_strategy is CreateStrategy.WEBDAV_MOVE:
            return True
        if self.create_strategy is CreateStrategy.CHECKED_ONE_SHOT:
            return False
        return write_with_if_not_exists
