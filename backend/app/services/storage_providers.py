"""Typed storage-provider catalogue and transport resolution."""

from __future__ import annotations

from enum import Enum
from importlib.util import find_spec
from pathlib import PurePosixPath
from typing import Annotated, Literal, get_args
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
    # Product support is independent from the measured storage safety tier.
    # Stable providers have the broadest lifecycle coverage; beta transports
    # remain selectable only when their optional dependency is installed.
    support_level: Literal["stable", "beta"] = "stable"
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
    access_key: str = Field(min_length=1, json_schema_extra={"secret": True})
    secret_key: str = Field(min_length=1, json_schema_extra={"secret": True})


class WebDAVProviderConfig(_ProviderConfig):
    provider: Literal["nextcloud", "webdav"]
    endpoint_url: str = Field(min_length=1)
    username: str = Field(min_length=1)
    password: str = Field(min_length=1, json_schema_extra={"secret": True})


class SFTPProviderConfig(_ProviderConfig):
    provider: Literal["sftp"]
    host: str = Field(min_length=1)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1)
    # Either a mounted known_hosts file path or one OpenSSH known-host entry.
    # Empty is accepted only so pre-host-key rows remain readable and can be
    # edited. ``resolve_transport`` is the activation boundary and rejects it,
    # so no SFTP connection can fall back to trust-on-first-use.
    host_key: str = ""
    password: str = Field(default="", json_schema_extra={"secret": True})
    private_key_path: str = ""
    passphrase: str = Field(default="", json_schema_extra={"secret": True})

    @model_validator(mode="after")
    def validate_auth(self):
        password = self.password.strip()
        key_path = self.private_key_path.strip()
        self.host_key = self.host_key.strip()
        if bool(password) == bool(key_path):
            raise ValueError("sftp_exactly_one_authentication_required")
        if self.passphrase and not key_path:
            raise ValueError("sftp_passphrase_requires_private_key")
        if "BEGIN " in key_path:
            raise ValueError("sftp_inline_private_key_forbidden")
        self.password = password
        self.private_key_path = key_path
        return self


_PROVIDER_CONFIG_MODELS = (
    LocalProviderConfig,
    S3ProviderConfig,
    WebDAVProviderConfig,
    SFTPProviderConfig,
)

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


def _secret_field_names(
    value: StorageProviderConfig | type[_ProviderConfig],
) -> set[str]:
    """Derive secret splitting from Pydantic field metadata.

    The compatibility set is retained only for old provider classes/config
    rows which predate metadata; new fields become secret by declaring
    ``json_schema_extra={"secret": True}``, so business logic need not be
    updated in a second location when a provider grows a credential.
    """
    model = value if isinstance(value, type) else type(value)
    names: set[str] = set()
    for name, field in model.model_fields.items():
        extra = field.json_schema_extra
        if isinstance(extra, dict) and extra.get("secret") is True:
            names.add(name)
    return names


def _provider_model(provider: object) -> type[_ProviderConfig] | None:
    for model in _PROVIDER_CONFIG_MODELS:
        if provider in get_args(model.model_fields["provider"].annotation):
            return model
    return None


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
    secret_fields = _secret_field_names(value)
    secrets = {
        name: str(raw.pop(name))
        for name in tuple(raw)
        if name in secret_fields and raw[name] not in (None, "")
    }
    return raw, secrets


def merge_provider_secrets(
    config: dict[str, object], secrets: dict[str, str]
) -> StorageProviderConfig:
    return parse_provider_config({**config, **secrets})


def sanitized_provider_config(
    config: dict[str, object], secrets: dict[str, str]
) -> dict[str, object]:
    model = _provider_model(config.get("provider"))
    secret_fields = _secret_field_names(model) if model is not None else set()
    configured_secrets = {
        name
        for name in secret_fields
        if config.get(name) not in (None, "") or secrets.get(name) not in (None, "")
    }
    return {
        **{name: value for name, value in config.items() if name not in secret_fields},
        "secret_fields_set": sorted(configured_secrets),
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
    options: dict[str, str | int | bool] = {
        "host": config.host,
        "port": config.port,
        "username": config.username,
        "root": root,
        "host_key": config.host_key,
    }
    if not config.host_key:
        raise ValueError("sftp_host_key_required")
    if config.password:
        options["password"] = config.password
    else:
        options["private_key_path"] = config.private_key_path
        if config.passphrase:
            options["passphrase"] = config.passphrase
    return TransportSpec(
        kind=TransportKind.SFTP,
        provider=config.provider,
        namespace=f"sftp/{root}",
        options=options,
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
            support_level="stable",
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
                support_level="stable" if provider_id == "s3" else "beta",
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
                expected_tier="guarded",
                expected_tier_note=(
                    "Publish uses WebDAV MOVE with `Overwrite: F`; purge is manual "
                    "and confirmed only."
                ),
                consequences=[
                    "Manual permanent deletion requires one-shot confirmation.",
                    "Scheduled storage purge is skipped.",
                ],
                documentation_url=f"/docs/storage-providers.md#{provider_id}",
                available=remote_available,
                selectable=remote_available,
                support_level="beta",
                disabled_reason=remote_reason,
                fields=remote_common,
            )
        )
    sftp_available = find_spec("asyncssh") is not None
    sftp_reason = (
        None
        if sftp_available
        else "Requires the full image"
        if not remote_available
        else "SFTP transport is unavailable in this full image"
    )
    entries.append(
        StorageProvider(
            id="sftp",
            label="SFTP",
            category=ProviderCategory.SFTP,
            description="NAS storage over SSH File Transfer Protocol.",
            expected_tier="guarded",
            expected_tier_note=(
                "Publish uses SSH exclusive create (`x` mode); `host_key` is required "
                "and purge is manual and confirmed only."
            ),
            consequences=[
                "Manual permanent deletion requires one-shot confirmation.",
                "Scheduled storage purge is skipped.",
            ],
            documentation_url="/docs/storage-providers.md#sftp",
            available=sftp_available,
            selectable=sftp_available,
            support_level="beta",
            disabled_reason=sftp_reason,
            fields=[
                _field("host", "Host", "SFTP hostname"),
                _field(
                    "host_key",
                    "Host key",
                    "OpenSSH known-hosts file path or entry; required for verification",
                    input_type="path",
                ),
                _field("port", "Port", "SFTP port", input_type="number", default=22),
                _field("username", "Username", "SFTP account username"),
                _field(
                    "password",
                    "Password",
                    "Use either a password or a mounted private-key path",
                    input_type="password",
                    required=False,
                    secret=True,
                ),
                _field(
                    "private_key_path",
                    "Private key path",
                    "Mounted service-key path; inline key material is forbidden",
                    input_type="path",
                    required=False,
                ),
                _field(
                    "passphrase",
                    "Key passphrase",
                    "Optional passphrase for the mounted private key",
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


def render_storage_provider_docs() -> str:
    """Render the public provider reference from the authoritative registry."""
    category_labels = {
        ProviderCategory.THIS_MACHINE: "This machine",
        ProviderCategory.S3_COMPATIBLE: "S3-compatible object storage",
        ProviderCategory.WEBDAV: "Nextcloud and WebDAV",
        ProviderCategory.SFTP: "NAS over SFTP",
    }
    lines = [
        "# Storage providers",
        "",
        "PrintStash probes the configured storage at startup. Support maturity and storage safety are separate: the expected tier below is guidance, while `/api/v1/health` and Settings report the measured active tier.",
        "",
        "| Provider | Category | Support | Expected tier | Configuration fields |",
        "| --- | --- | --- | --- | --- |",
    ]
    for provider in provider_catalogue():
        fields = ", ".join(
            f"`{field.name}`" + (" (secret)" if field.secret else "")
            for field in provider.fields
        )
        lines.append(
            f"| [{provider.label}](#{provider.id}) | {category_labels[provider.category]} | {provider.support_level.title()} | {provider.expected_tier.title()} | {fields} |"
        )
    lines.extend(
        [
            "",
            "## Safety tiers",
            "",
            "- **Verified** storage proves conditional creation, replacement identity, and deletion identity. Automated storage-backed purge is allowed.",
            "- **Guarded** storage proves unique creation but lacks at least one destructive-operation proof. Manual permanent deletion requires one-shot confirmation; scheduled storage purge is skipped.",
            "- **Unguarded** storage cannot prove unique creation. Startup additionally requires `VAULT_STORAGE_ALLOW_UNVERIFIED=true`.",
            "",
            "Directory `fsync` support is diagnostic only. Local paths on network or unknown filesystems are capped at Guarded even when hardlinks work.",
            "",
        ]
    )
    for provider in provider_catalogue():
        lines.extend(
            [
                f"## {provider.id}",
                "",
                provider.description,
                "",
                f"Expected tier: **{provider.expected_tier.title()}**. {provider.expected_tier_note}",
                "",
            ]
        )
    lines.extend(
        [
            "## Credentials and upgrades",
            "",
            "Secrets are write-only: configuration reads expose only which secret fields are set. SFTP accepts exactly one authentication mode: password, or a mounted private-key path with an optional passphrase. Inline private-key material is rejected. New and updated SFTP configurations require `host_key` as either a mounted known-hosts path or an OpenSSH known-host entry; legacy rows without it remain readable but cannot activate until it is added.",
            "",
            "PrintStash never creates an S3 bucket or changes its lifecycle policy. Grant data-plane access plus read-only bucket/versioning/lifecycle inspection; remove `s3:CreateBucket` and `s3:PutLifecycleConfiguration` from older policies.",
            "",
            "New deployments should select and save a provider through Setup or Settings.\nEnvironment-only deployments use scalar fields: `VAULT_STORAGE_PROVIDER` and\n`VAULT_STORAGE_ROOT`, plus `VAULT_S3_*`, `VAULT_WEBDAV_*`, or `VAULT_SFTP_*`\nfor the selected transport. `VAULT_STORAGE_PROVIDER_CONFIG` and\n`VAULT_STORAGE_PROVIDER_SECRETS` remain compatibility inputs but are deprecated.",
            "",
            "The checked-in Compose files forward the legacy/local and `VAULT_S3_*` fields,\nbut do not automatically forward `VAULT_STORAGE_PROVIDER`,\n`VAULT_STORAGE_ROOT`, `VAULT_WEBDAV_*`, `VAULT_SFTP_*`, or\n`VAULT_STORAGE_ALLOW_UNVERIFIED` from `.env`. When configuring those fields\nentirely through environment variables, add them explicitly under the API\nservice's `environment` in a Compose override. Configuration saved through the\nSetup or Settings UI does not need that override.",
            "",
            "`VAULT_STORAGE_BACKEND` and the legacy S3 variables remain supported upgrade\ninputs. Keep them unchanged for the first 0.13.0 compatibility boot.",
        ]
    )
    return "\n".join(lines) + "\n"
