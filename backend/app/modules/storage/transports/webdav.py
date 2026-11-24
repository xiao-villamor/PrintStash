"""WebDAV collections, including Nextcloud, through OpenDAL.

OpenDAL stages bytes; create-only publication is one protocol ``MOVE`` with
``Overwrite: F`` and listing is a bounded one-directory ``PROPFIND``, because
the binding exposes neither primitive.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal
from urllib.parse import quote

from app.modules.storage.target_identity import (
    StorageTargetIdentity,
    locator_endpoint,
    normalized_endpoint,
)
from app.modules.storage.transports.base import (
    CreateStrategy,
    ListingStrategy,
    OpenDALService,
    ProviderCategory,
    ProviderConfig,
    ProviderPresentation,
    RemoteTransport,
    TransportKind,
    TransportSpec,
    config_field,
    endpoint_with_path,
    normalize_root,
)


class WebDAVProviderConfig(ProviderConfig):
    provider: Literal[
        "nextcloud",
        "webdav",
        "synology_webdav",
        "qnap_webdav",
        "hetzner_storage_box_webdav",
        "koofr",
    ]
    endpoint_url: str = config_field(
        "Server URL",
        "Nextcloud base URL or full WebDAV endpoint",
        input_type="url",
        min_length=1,
    )
    username: str = config_field("Username", "Remote account username", min_length=1)
    password: str = config_field(
        "Password", "Remote account password", secret=True, min_length=1
    )


def _webdav_provider(id: str, label: str) -> ProviderPresentation:
    return ProviderPresentation(
        id=id,
        label=label,
        category=ProviderCategory.WEBDAV,
        description="Remote storage over WebDAV.",
        expected_tier="guarded",
        expected_tier_note="Confirmed catalog removal retains stored bytes; exact "
        "physical deletion is unavailable.",
        consequences=(
            "Confirmed catalog removal retains stored bytes.",
            "Automatic physical deletion is unavailable.",
        ),
        support_level="beta",
    )


_SERVICE = OpenDALService(
    scheme="webdav",
    label="WebDAV",
    probe_options={"endpoint": "https://availability.invalid"},
)


class WebDAVTransport(RemoteTransport[WebDAVProviderConfig]):
    kind = TransportKind.WEBDAV
    config_model = WebDAVProviderConfig
    default_provider = "webdav"
    providers = (
        _webdav_provider("nextcloud", "Nextcloud"),
        _webdav_provider("webdav", "WebDAV"),
    )
    preset_category = ProviderCategory.WEBDAV
    preset_description = "Remote storage over WebDAV."
    vault_runtime = _SERVICE
    remote_runtime = _SERVICE
    create_strategy = CreateStrategy.WEBDAV_MOVE
    listing = ListingStrategy.WEBDAV_PROPFIND
    atomic_publication = True
    large_object_note = (
        "bounded streaming/materialization with proxy delivery; provider limits apply."
    )

    def resolve(self, config: WebDAVProviderConfig) -> TransportSpec:
        root = normalize_root(config.root)
        if config.provider == "nextcloud":
            endpoint = endpoint_with_path(
                config.endpoint_url,
                f"remote.php/dav/files/{quote(config.username, safe='')}",
            )
        else:
            endpoint = endpoint_with_path(config.endpoint_url, "")
        return TransportSpec(
            kind=self.kind,
            provider=config.provider,
            namespace=f"webdav/{root}",
            options={
                "endpoint_url": endpoint.rstrip("/"),
                "username": config.username,
                "password": config.password,
                "root": root,
            },
        )

    def operator_options(self, spec: TransportSpec) -> dict[str, str]:
        options = spec.options
        return {
            "endpoint": str(options["endpoint_url"]),
            "root": str(options["root"]),
            "username": str(options["username"]),
            "password": str(options["password"]),
        }

    def target(self, options: Mapping[str, object]) -> StorageTargetIdentity:
        return StorageTargetIdentity(
            transport=self.kind.value,
            endpoint=normalized_endpoint(str(options["endpoint_url"])),
        )

    def provider_ref_fields(
        self,
        *,
        options: Mapping[str, object],
        backend: object,
        namespace: str,
    ) -> dict[str, object]:
        # OpenDAL's namespace is only the managed root. The endpoint is the
        # actual destination and must be included, while credentials remain
        # deliberately absent from the identity.
        webdav_endpoint = getattr(backend, "_webdav_endpoint", None)
        if webdav_endpoint is None:
            webdav_endpoint = options.get("endpoint_url")
        return {
            "endpoint": locator_endpoint(webdav_endpoint),
            "root": str(
                getattr(backend, "_webdav_root", None)
                or options.get("root")
                or namespace
            )
            .strip()
            .strip("/"),
        }
