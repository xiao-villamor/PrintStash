"""Typed storage-provider catalogue and transport resolution."""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, get_args
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.services.storage_operations import UseAvailability, use_availability


class ProviderCategory(str, Enum):
    THIS_MACHINE = "this_machine"
    S3_COMPATIBLE = "s3_compatible"
    WEBDAV = "nextcloud_webdav"
    SFTP = "nas_sftp"
    CONSUMER_CLOUD = "consumer_cloud"


class TransportKind(str, Enum):
    LOCAL = "local"
    S3 = "s3"
    WEBDAV = "webdav"
    SFTP = "sftp"
    GDRIVE = "gdrive"


class _FieldMetadata(BaseModel):
    secret: bool = False
    input_type: Literal["text", "password", "url", "number", "path"] = "text"
    visible_for: list[str] = Field(default_factory=list)
    required_for: list[str] = Field(default_factory=list)


class StorageFieldDescriptor(BaseModel):
    name: str
    label: str
    help: str
    input_type: Literal["text", "password", "url", "number", "path"] = "text"
    required: bool = True
    secret: bool = False
    default: str | int | None = None
    options: list[str] = Field(default_factory=list)


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
    fields_by_use: dict[str, list[StorageFieldDescriptor]] = Field(default_factory=dict)
    requirements: list[dict[str, object]] = Field(default_factory=list)
    transport: str = ""
    uses: dict[str, UseAvailability] = Field(default_factory=dict)


def _config_field(
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


class _ProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: str = _config_field(
        "Root", "Dedicated folder or prefix", default="vault-data", input_type="path"
    )

    @model_validator(mode="after")
    def validate_root(self):
        self.root = normalize_root(self.root)
        return self


class LocalProviderConfig(_ProviderConfig):
    provider: Literal["local"]
    data_dir: str = _config_field(
        "Models directory", "Directory for model files", input_type="path"
    )
    thumb_dir: str = _config_field(
        "Thumbnail directory", "Directory for generated images", input_type="path"
    )


class S3ProviderConfig(_ProviderConfig):
    provider: Literal["s3", "cloudflare_r2", "backblaze_b2", "wasabi", "s3_self_hosted"]
    bucket: str = _config_field("Bucket", "Existing bucket name", min_length=1)
    region: str = _config_field(
        "Region",
        "Provider region; required for Backblaze B2 and Wasabi",
        default="auto",
    )
    addressing_style: Literal["auto", "path", "virtual"] = _config_field(
        "Addressing style", "Auto uses the provider preset", default="auto"
    )
    endpoint_url: str = _config_field(
        "Endpoint",
        "S3-compatible server URL",
        default="",
        input_type="url",
        visible_for=("s3", "s3_self_hosted"),
        required_for=("s3_self_hosted",),
    )
    account_id: str = _config_field(
        "Account ID",
        "Cloudflare account ID",
        default="",
        visible_for=("cloudflare_r2",),
        required_for=("cloudflare_r2",),
    )
    access_key: str = _config_field(
        "Access key", "Object-storage access key", secret=True, min_length=1
    )
    secret_key: str = _config_field(
        "Secret key", "Object-storage secret key", secret=True, min_length=1
    )


class WebDAVProviderConfig(_ProviderConfig):
    provider: Literal["nextcloud", "webdav"]
    endpoint_url: str = _config_field(
        "Server URL",
        "Nextcloud base URL or full WebDAV endpoint",
        input_type="url",
        min_length=1,
    )
    username: str = _config_field("Username", "Remote account username", min_length=1)
    password: str = _config_field(
        "Password", "Remote account password", secret=True, min_length=1
    )


class SFTPProviderConfig(_ProviderConfig):
    provider: Literal["sftp"]
    host: str = _config_field("Host", "SFTP hostname", min_length=1)
    port: int = _config_field(
        "Port", "SFTP port", default=22, input_type="number", ge=1, le=65535
    )
    username: str = _config_field("Username", "SFTP account username", min_length=1)
    # Either a mounted known_hosts file path or one OpenSSH known-host entry.
    # Empty is accepted only so pre-host-key rows remain readable and can be
    # edited. ``resolve_transport`` is the activation boundary and rejects it,
    # so no SFTP connection can fall back to trust-on-first-use.
    host_key: str = _config_field(
        "Host key",
        "OpenSSH known-hosts path or entry",
        default="",
        input_type="path",
        required_for=("sftp",),
    )
    password: str = _config_field(
        "Password",
        "Use either a password or a mounted private-key path",
        default="",
        secret=True,
    )
    private_key_path: str = _config_field(
        "Private key path",
        "Mounted service-key path; inline key material is forbidden",
        default="",
        input_type="path",
    )
    passphrase: str = _config_field(
        "Key passphrase",
        "Optional passphrase for the mounted private key",
        default="",
        secret=True,
    )

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


class GoogleDriveProviderConfig(_ProviderConfig):
    provider: Literal["gdrive"]
    client_id: str = _config_field(
        "OAuth client ID", "Google OAuth application client ID", min_length=1
    )
    client_secret: str = _config_field(
        "OAuth client secret",
        "Google OAuth application client secret",
        secret=True,
        min_length=1,
    )
    refresh_token: str = _config_field(
        "Refresh token", "OAuth refresh token", secret=True, min_length=1
    )


_PROVIDER_CONFIG_MODELS = (
    LocalProviderConfig,
    S3ProviderConfig,
    WebDAVProviderConfig,
    SFTPProviderConfig,
    GoogleDriveProviderConfig,
)

StorageProviderConfig = Annotated[
    LocalProviderConfig
    | S3ProviderConfig
    | WebDAVProviderConfig
    | SFTPProviderConfig
    | GoogleDriveProviderConfig,
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


def provider_secret_fields(provider: str) -> set[str]:
    model = _provider_model(provider)
    return _secret_field_names(model) if model is not None else set()


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
    secrets = {}
    for name in secret_fields:
        value = raw.pop(name, None)
        if value not in (None, ""):
            secrets[name] = str(value)
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
        addressing_style = config.addressing_style
        if config.provider == "s3_self_hosted" and not endpoint:
            raise ValueError("s3_endpoint_required")
        if addressing_style == "auto" and config.provider == "s3_self_hosted":
            addressing_style = "path"
        path_style = addressing_style == "path"
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
                "addressing_style": addressing_style,
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
    if isinstance(config, GoogleDriveProviderConfig):
        return TransportSpec(
            kind=TransportKind.GDRIVE,
            provider=config.provider,
            namespace=f"gdrive/{root}",
            options={
                "root": root,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": config.refresh_token,
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


_PROVIDER_PRESENTATION = [
    {
        "id": "local",
        "label": "This machine",
        "category": "this_machine",
        "description": "Local filesystem directories.",
        "expected_tier": "verified",
        "expected_tier_note": "Verified on local filesystems with working hardlinks.",
        "consequences": [],
        "documentation_url": "/docs/storage-providers.md#local",
        "support_level": "stable",
    },
    {
        "id": "s3",
        "label": "Amazon S3 or compatible",
        "category": "s3_compatible",
        "description": "Native S3-compatible object storage.",
        "expected_tier": "guarded",
        "expected_tier_note": "Verified when bucket versioning is enabled; otherwise "
        "Guarded.",
        "consequences": ["Automated purge requires a Verified probe."],
        "documentation_url": "/docs/storage-providers.md#s3",
        "support_level": "stable",
    },
    {
        "id": "cloudflare_r2",
        "label": "Cloudflare R2",
        "category": "s3_compatible",
        "description": "Native S3-compatible object storage.",
        "expected_tier": "guarded",
        "expected_tier_note": "Verified when bucket versioning is enabled; otherwise "
        "Guarded.",
        "consequences": ["Automated purge requires a Verified probe."],
        "documentation_url": "/docs/storage-providers.md#cloudflare_r2",
        "support_level": "beta",
    },
    {
        "id": "backblaze_b2",
        "label": "Backblaze B2",
        "category": "s3_compatible",
        "description": "Native S3-compatible object storage.",
        "expected_tier": "guarded",
        "expected_tier_note": "Verified when bucket versioning is enabled; otherwise "
        "Guarded.",
        "consequences": ["Automated purge requires a Verified probe."],
        "documentation_url": "/docs/storage-providers.md#backblaze_b2",
        "support_level": "beta",
    },
    {
        "id": "wasabi",
        "label": "Wasabi",
        "category": "s3_compatible",
        "description": "Native S3-compatible object storage.",
        "expected_tier": "guarded",
        "expected_tier_note": "Verified when bucket versioning is enabled; otherwise "
        "Guarded.",
        "consequences": ["Automated purge requires a Verified probe."],
        "documentation_url": "/docs/storage-providers.md#wasabi",
        "support_level": "beta",
    },
    {
        "id": "s3_self_hosted",
        "label": "Self-hosted S3",
        "category": "s3_compatible",
        "description": "Native S3-compatible object storage.",
        "expected_tier": "guarded",
        "expected_tier_note": "Verified when bucket versioning is enabled; otherwise "
        "Guarded.",
        "consequences": ["Automated purge requires a Verified probe."],
        "documentation_url": "/docs/storage-providers.md#s3_self_hosted",
        "support_level": "beta",
    },
    {
        "id": "nextcloud",
        "label": "Nextcloud",
        "category": "nextcloud_webdav",
        "description": "Remote storage over WebDAV.",
        "expected_tier": "guarded",
        "expected_tier_note": "Confirmed catalog removal retains stored bytes; exact "
        "physical deletion is unavailable.",
        "consequences": [
            "Confirmed catalog removal retains stored bytes.",
            "Automatic physical deletion is unavailable.",
        ],
        "documentation_url": "/docs/storage-providers.md#nextcloud",
        "support_level": "beta",
    },
    {
        "id": "webdav",
        "label": "WebDAV",
        "category": "nextcloud_webdav",
        "description": "Remote storage over WebDAV.",
        "expected_tier": "guarded",
        "expected_tier_note": "Confirmed catalog removal retains stored bytes; exact "
        "physical deletion is unavailable.",
        "consequences": [
            "Confirmed catalog removal retains stored bytes.",
            "Automatic physical deletion is unavailable.",
        ],
        "documentation_url": "/docs/storage-providers.md#webdav",
        "support_level": "beta",
    },
    {
        "id": "sftp",
        "label": "SFTP",
        "category": "nas_sftp",
        "description": "NAS storage over SSH File Transfer Protocol.",
        "expected_tier": "guarded",
        "expected_tier_note": "Publish uses SSH exclusive create (`x` mode); `host_key` is "
        "required and confirmed catalog purge retains stored bytes.",
        "consequences": [
            "Confirmed catalog removal retains stored bytes.",
            "Automatic physical deletion is unavailable.",
        ],
        "documentation_url": "/docs/storage-providers.md#sftp",
        "support_level": "beta",
    },
    {
        "id": "gdrive",
        "label": "Google Drive",
        "category": "consumer_cloud",
        "description": "Consumer cloud storage through Apache OpenDAL.",
        "expected_tier": "unguarded",
        "expected_tier_note": "Available for read-only Library sources and off-site backup "
        "replicas; not selectable as managed Vault storage.",
        "consequences": [
            "Remote backup retention is manual because conditional delete is "
            "unavailable.",
            "Google Drive replicas do not authorize automatic Vault garbage "
            "collection.",
        ],
        "documentation_url": "/docs/storage-providers.md#gdrive",
        "support_level": "beta",
    },
]


def provider_fields(
    provider: str, *, use: str = "vault"
) -> list[StorageFieldDescriptor]:
    model = _provider_model(provider)
    if model is None or use not in {"vault", "library", "backup"}:
        raise ValueError("storage_provider_unknown")
    fields = []
    for name, field in model.model_fields.items():
        if name == "provider":
            continue
        extra = _FieldMetadata.model_validate(field.json_schema_extra or {})
        visible = extra.visible_for
        if visible and provider not in visible:
            continue
        default = None if field.is_required() else field.default
        required = field.is_required() or provider in extra.required_for
        label = field.title or name
        if name == "root":
            required = True
            if use != "vault":
                label, default = "Base folder", "PrintStash"
            elif provider == "gdrive":
                label, default = "Folder", "PrintStash"
        fields.append(
            StorageFieldDescriptor(
                name=name,
                label=label,
                help=field.description or "",
                input_type=extra.input_type,
                required=required,
                secret=extra.secret,
                default=default,
                options=list(get_args(field.annotation))
                if getattr(field.annotation, "__origin__", None) is Literal
                else [],
            )
        )
    return fields


def provider_catalogue() -> list[StorageProvider]:
    entries = []
    for presentation in _PROVIDER_PRESENTATION:
        provider_id = presentation["id"]
        model = _provider_model(provider_id)
        transport = (
            "s3"
            if model is S3ProviderConfig
            else "webdav"
            if model is WebDAVProviderConfig
            else provider_id
        )
        uses = {
            use: use_availability(transport, use)
            for use in ("vault", "library", "backup")
        }
        vault = uses["vault"]
        requirements = []
        if model is SFTPProviderConfig:
            requirements = [
                {
                    "kind": "exactly_one",
                    "fields": ["password", "private_key_path"],
                    "message": "Use either a password or a private key path.",
                },
                {
                    "kind": "requires",
                    "fields": ["passphrase", "private_key_path"],
                    "message": "A key passphrase requires a private key path.",
                },
            ]
        if provider_id in {"backblaze_b2", "wasabi"}:
            requirements = [
                {
                    "kind": "not_value",
                    "fields": ["region"],
                    "value": "auto",
                    "message": "Enter the provider region.",
                }
            ]
        entries.append(
            StorageProvider(
                **presentation,
                transport=transport,
                available=vault.dependency_installed and vault.service_compiled,
                selectable=vault.available,
                disabled_reason=None if vault.available else vault.reason,
                uses=uses,
                fields=provider_fields(provider_id),
                fields_by_use={
                    use: provider_fields(provider_id, use=use) for use in uses
                },
                requirements=requirements,
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
        ProviderCategory.CONSUMER_CLOUD: "Consumer cloud storage",
    }
    lines = [
        "# Storage providers",
        "",
        "This page configures **managed Vault storage**, where PrintStash owns object\ncreation and cleanup. To index files already owned by a NAS, S3 bucket, WebDAV\ncollection or SFTP directory, use a read-only\n[Library source](./library-sources.md). Reusing the same server does not merge\nthe two ownership domains.",
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
        if provider.id == "s3":
            lines.extend(
                [
                    "Use the concrete AWS region for Amazon S3. Leave `endpoint_url` empty and keep\n`addressing_style=auto` unless the account has a specific endpoint requirement.\nFor self-hosted S3, `addressing_style=auto` resolves to path style because many\nNAS and local object stores do not provide wildcard bucket DNS. Select\n`virtual` only when the endpoint, DNS and TLS certificate support virtual-host\nbucket names.",
                    "",
                    "The startup probe creates and cleans up a unique probe object. When the server\nreturns a VersionId, cleanup targets that exact version. It never deletes a\nsame-key replacement by an external writer.",
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
            "",
            "Changing from the legacy `s3` input to a typed provider does not move bytes.\nAdopt only an equivalent bucket, endpoint, region, addressing style and root.\nThere is no general provider-to-provider byte migration in 0.13.0.",
        ]
    )
    return "\n".join(lines) + "\n"
