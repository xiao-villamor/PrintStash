"""Typed storage-provider catalogue and transport resolution."""

from __future__ import annotations

from enum import Enum
from importlib.util import find_spec
from pathlib import PurePosixPath
from typing import Annotated, Literal
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class ProviderCategory(str, Enum):
    THIS_MACHINE = "this_machine"
    S3_COMPATIBLE = "s3_compatible"
    WEBDAV = "nextcloud_webdav"
    SFTP = "nas_sftp"


class TransportKind(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    WEBDAV = "webdav"
    SFTP = "sftp"


class StorageFieldDescriptor(BaseModel):
    name: str
    label: str
    help: str
    input_type: Literal["text", "password", "url", "number", "path"] = "text"
    required: bool = True
    secret: bool = False
    default: str | int | None = None


class StorageProvider(BaseModel):
    id: str
    label: str
    category: ProviderCategory
    description: str
    expected_tier: Literal["verified", "guarded", "unguarded"]
    expected_tier_note: str
    consequences: list[str]
    documentation_url: str
    available: bool
    selectable: bool
    disabled_reason: str | None = None
    fields: list[StorageFieldDescriptor]


class _ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str = "vault-data"

    @model_validator(mode="after")
    def validate_root(self):
        self.root = normalize_root(self.root)
        return self


class LocalProviderConfig(_ProviderConfig):
    provider: Literal["local"]
    data_dir: str
    thumb_dir: str


class S3ProviderConfig(_ProviderConfig):
    provider: Literal["s3", "cloudflare_r2", "backblaze_b2", "wasabi", "s3_self_hosted"]
    bucket: str = Field(min_length=1)
    region: str = "auto"
    endpoint_url: str = ""
    account_id: str = ""
    access_key: str = Field(min_length=1)
    secret_key: str = Field(min_length=1)


class WebDAVProviderConfig(_ProviderConfig):
    provider: Literal["nextcloud", "webdav"]
    endpoint_url: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class SFTPProviderConfig(_ProviderConfig):
    provider: Literal["sftp"]
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    password: str | None = None
    private_key_path: str | None = None
    passphrase: str | None = None

    @model_validator(mode="after")
    def validate_auth(self):
        password = bool(self.password)
        key_path = bool(self.private_key_path)
        if password == key_path:
            raise ValueError("sftp_exactly_one_auth_method_required")
        if self.passphrase and not key_path:
            raise ValueError("sftp_passphrase_requires_private_key_path")
        if self.private_key_path and "BEGIN " in self.private_key_path:
            raise ValueError("sftp_inline_private_key_forbidden")
        return self


StorageProviderConfig = Annotated[
    LocalProviderConfig | S3ProviderConfig | WebDAVProviderConfig | SFTPProviderConfig,
    Field(discriminator="provider"),
]
_CONFIG_ADAPTER = TypeAdapter(StorageProviderConfig)


class TransportSpec(BaseModel):
    kind: TransportKind
    provider: str
    namespace: str
    options: dict[str, str | int | bool]


_SECRET_FIELDS = {"access_key", "secret_key", "password", "passphrase"}


def normalize_root(value: str) -> str:
    root = value.strip().strip("/")
    if not root or root in {".", ".."}:
        raise ValueError("storage_root_required")
    path = PurePosixPath(root)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("storage_root_traversal_forbidden")
    return path.as_posix()


def parse_provider_config(value: object) -> StorageProviderConfig:
    return _CONFIG_ADAPTER.validate_python(value)


def split_provider_config(
    value: StorageProviderConfig,
) -> tuple[dict[str, object], dict[str, str]]:
    raw = value.model_dump(mode="json")
    secrets = {
        name: str(raw.pop(name))
        for name in tuple(raw)
        if name in _SECRET_FIELDS and raw[name] not in (None, "")
    }
    return raw, secrets


def merge_provider_secrets(
    config: dict[str, object], secrets: dict[str, str]
) -> StorageProviderConfig:
    return parse_provider_config({**config, **secrets})


def sanitized_provider_config(
    config: dict[str, object], secrets: dict[str, str]
) -> dict[str, object]:
    return {
        **config,
        "secret_fields_set": sorted(name for name, value in secrets.items() if value),
    }


def _endpoint_with_path(endpoint: str, path: str) -> str:
    parts = urlsplit(endpoint.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ValueError("provider_endpoint_invalid")
    base = urlunsplit(
        (parts.scheme, parts.netloc, parts.path.rstrip("/") + "/", "", "")
    )
    return urljoin(base, path.lstrip("/"))


def resolve_transport(config: StorageProviderConfig) -> TransportSpec:
    root = normalize_root(config.root)
    if isinstance(config, LocalProviderConfig):
        return TransportSpec(
            kind=TransportKind.LOCAL,
            provider=config.provider,
            namespace=f"local/{root}",
            options={
                "data_dir": config.data_dir,
                "thumb_dir": config.thumb_dir,
                "root": root,
            },
        )
    if isinstance(config, S3ProviderConfig):
        endpoint = config.endpoint_url.strip()
        region = config.region.strip() or "auto"
        path_style = config.provider == "s3_self_hosted"
        if config.provider == "cloudflare_r2":
            if not config.account_id.strip():
                raise ValueError("r2_account_id_required")
            endpoint = f"https://{config.account_id.strip()}.r2.cloudflarestorage.com"
            region = "auto"
        elif config.provider == "backblaze_b2":
            if region == "auto":
                raise ValueError("b2_region_required")
            endpoint = f"https://s3.{region}.backblazeb2.com"
        elif config.provider == "wasabi":
            if region == "auto":
                raise ValueError("wasabi_region_required")
            endpoint = f"https://s3.{region}.wasabisys.com"
        return TransportSpec(
            kind=TransportKind.S3,
            provider=config.provider,
            namespace=f"s3/{config.bucket}/{root}",
            options={
                "bucket": config.bucket,
                "endpoint_url": endpoint,
                "region": region,
                "root": root,
                "path_style": path_style,
                "access_key": config.access_key,
                "secret_key": config.secret_key,
            },
        )
    if isinstance(config, WebDAVProviderConfig):
        endpoint = config.endpoint_url
        if config.provider == "nextcloud":
            endpoint = _endpoint_with_path(
                endpoint, f"remote.php/dav/files/{quote(config.username, safe='')}"
            )
        else:
            endpoint = _endpoint_with_path(endpoint, "")
        return TransportSpec(
            kind=TransportKind.WEBDAV,
            provider=config.provider,
            namespace=f"webdav/{root}",
            options={
                "endpoint_url": endpoint.rstrip("/"),
                "username": config.username,
                "password": config.password,
                "root": root,
            },
        )
    return TransportSpec(
        kind=TransportKind.SFTP,
        provider=config.provider,
        namespace=f"sftp/{root}",
        options={
            "host": config.host,
            "port": config.port,
            "username": config.username,
            "root": root,
            **({"password": config.password} if config.password else {}),
            **(
                {"private_key_path": config.private_key_path}
                if config.private_key_path
                else {}
            ),
            **({"passphrase": config.passphrase} if config.passphrase else {}),
        },
    )


def _field(
    name: str,
    label: str,
    help_text: str,
    *,
    input_type: Literal["text", "password", "url", "number", "path"] = "text",
    required: bool = True,
    secret: bool = False,
    default: str | int | None = None,
) -> StorageFieldDescriptor:
    return StorageFieldDescriptor(
        name=name,
        label=label,
        help=help_text,
        input_type=input_type,
        required=required,
        secret=secret,
        default=default,
    )


def provider_catalogue() -> list[StorageProvider]:
    remote_available = find_spec("opendal") is not None
    remote_reason = None if remote_available else "Requires the full image"
    common_s3 = [
        _field("bucket", "Bucket", "Existing bucket name"),
        _field("region", "Region", "Provider region", default="auto"),
        _field("root", "Root", "Non-empty managed prefix", default="vault-data"),
        _field("access_key", "Access key", "Object-storage access key", secret=True),
        _field(
            "secret_key",
            "Secret key",
            "Object-storage secret key",
            input_type="password",
            secret=True,
        ),
    ]
    entries: list[StorageProvider] = [
        StorageProvider(
            id="local",
            label="This machine",
            category=ProviderCategory.THIS_MACHINE,
            description="Local filesystem directories.",
            expected_tier="verified",
            expected_tier_note="Verified on local filesystems with working hardlinks.",
            consequences=[],
            documentation_url="/docs/storage-providers.md#local",
            available=True,
            selectable=True,
            fields=[
                _field(
                    "data_dir",
                    "Models directory",
                    "Directory for model files",
                    input_type="path",
                ),
                _field(
                    "thumb_dir",
                    "Thumbnail directory",
                    "Directory for generated images",
                    input_type="path",
                ),
                _field("root", "Root", "Ownership namespace", default="vault-data"),
            ],
        )
    ]
    for provider_id, label, extras in (
        (
            "s3",
            "Amazon S3 or compatible",
            [
                _field(
                    "endpoint_url",
                    "Endpoint",
                    "Optional S3-compatible endpoint",
                    input_type="url",
                    required=False,
                )
            ],
        ),
        (
            "cloudflare_r2",
            "Cloudflare R2",
            [_field("account_id", "Account ID", "Cloudflare account ID")],
        ),
        ("backblaze_b2", "Backblaze B2", []),
        ("wasabi", "Wasabi", []),
        (
            "s3_self_hosted",
            "Self-hosted S3",
            [
                _field(
                    "endpoint_url",
                    "Endpoint",
                    "MinIO, Garage, or SeaweedFS endpoint",
                    input_type="url",
                )
            ],
        ),
    ):
        entries.append(
            StorageProvider(
                id=provider_id,
                label=label,
                category=ProviderCategory.S3_COMPATIBLE,
                description="Native S3-compatible object storage.",
                expected_tier="guarded",
                expected_tier_note="Verified when bucket versioning is enabled; otherwise Guarded.",
                consequences=["Automated purge requires a Verified probe."],
                documentation_url=f"/docs/storage-providers.md#{provider_id}",
                available=True,
                selectable=True,
                fields=[*common_s3, *extras],
            )
        )
    remote_common = [
        _field("endpoint_url", "Server URL", "HTTPS server endpoint", input_type="url"),
        _field("username", "Username", "Remote account username"),
        _field(
            "password",
            "Password",
            "Remote account password",
            input_type="password",
            secret=True,
        ),
        _field("root", "Root", "Non-empty managed folder", default="vault-data"),
    ]
    for provider_id, label in (("nextcloud", "Nextcloud"), ("webdav", "WebDAV")):
        entries.append(
            StorageProvider(
                id=provider_id,
                label=label,
                category=ProviderCategory.WEBDAV,
                description="Remote storage over WebDAV.",
                expected_tier="unguarded",
                expected_tier_note="Remote rename does not prove conditional ownership.",
                consequences=[
                    "Startup acknowledgement and purge confirmation are required."
                ],
                documentation_url=f"/docs/storage-providers.md#{provider_id}",
                available=remote_available,
                selectable=remote_available,
                disabled_reason=remote_reason,
                fields=remote_common,
            )
        )
    sftp_available = remote_available
    if remote_available:
        from app.services.storage_opendal import opendal_transport_available

        sftp_available = opendal_transport_available(TransportKind.SFTP)
    entries.append(
        StorageProvider(
            id="sftp",
            label="SFTP",
            category=ProviderCategory.SFTP,
            description="NAS storage over SSH File Transfer Protocol.",
            expected_tier="unguarded",
            expected_tier_note="SFTP cannot prove conditional ownership.",
            consequences=[
                "Startup acknowledgement and purge confirmation are required."
            ],
            documentation_url="/docs/storage-providers.md#sftp",
            available=sftp_available,
            selectable=sftp_available,
            disabled_reason=(
                remote_reason
                if not remote_available
                else None
                if sftp_available
                else "SFTP transport is unavailable in this full image"
            ),
            fields=[
                _field("host", "Host", "SFTP hostname"),
                _field("port", "Port", "SFTP port", input_type="number", default=22),
                _field("username", "Username", "SFTP account username"),
                _field(
                    "password",
                    "Password",
                    "Use password or a mounted key path",
                    input_type="password",
                    required=False,
                    secret=True,
                ),
                _field(
                    "private_key_path",
                    "Private key path",
                    "Mounted key path; inline key material is forbidden",
                    input_type="path",
                    required=False,
                ),
                _field(
                    "passphrase",
                    "Key passphrase",
                    "Optional mounted-key passphrase",
                    input_type="password",
                    required=False,
                    secret=True,
                ),
                _field(
                    "root", "Root", "Non-empty managed folder", default="vault-data"
                ),
            ],
        )
    )
    return entries
