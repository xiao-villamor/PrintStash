"""Google Drive through OpenDAL: Library sources and backup replicas only."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from app.modules.storage.target_identity import StorageTargetIdentity
from app.modules.storage.transports.base import (
    CreateStrategy,
    OpenDALService,
    ProviderCategory,
    ProviderConfig,
    ProviderPresentation,
    RemoteTransport,
    TransportKind,
    TransportSpec,
    config_field,
    normalize_root,
)


class GoogleDriveProviderConfig(ProviderConfig):
    provider: Literal["gdrive"]
    client_id: str = config_field(
        "OAuth client ID", "Google OAuth application client ID", min_length=1
    )
    client_secret: str = config_field(
        "OAuth client secret",
        "Google OAuth application client secret",
        secret=True,
        min_length=1,
    )
    refresh_token: str = config_field(
        "Refresh token", "OAuth refresh token", secret=True, min_length=1
    )


class GoogleDriveTransport(RemoteTransport[GoogleDriveProviderConfig]):
    kind = TransportKind.GDRIVE
    config_model = GoogleDriveProviderConfig
    default_provider = "gdrive"
    providers = (
        ProviderPresentation(
            id="gdrive",
            label="Google Drive",
            category=ProviderCategory.CONSUMER_CLOUD,
            description="Consumer cloud storage through Apache OpenDAL.",
            expected_tier="unguarded",
            expected_tier_note="Available for read-only Library sources and off-site "
            "backup replicas; not selectable as managed Vault storage.",
            consequences=(
                "Remote backup retention is manual because conditional delete is "
                "unavailable.",
                "Google Drive replicas do not authorize automatic Vault garbage "
                "collection.",
            ),
            support_level="beta",
        ),
    )
    preset_category = ProviderCategory.CONSUMER_CLOUD
    preset_description = "Consumer cloud storage."
    # Drive has no create-only write or immutable delete identity.
    vault_runtime = None
    remote_runtime = OpenDALService(
        scheme="gdrive",
        label="Google Drive",
        probe_options={"access_token": "availability-only"},
    )
    # OpenDAL's Drive service uses a one-shot writer: a second ``write`` fails
    # even though the first chunk was accepted.
    create_strategy = CreateStrategy.CHECKED_ONE_SHOT
    secrets_select_account = True
    large_object_note = (
        "bounded materialization/readback with proxy delivery; provider quotas "
        "and throttling apply."
    )

    def resolve(self, config: GoogleDriveProviderConfig) -> TransportSpec:
        root = normalize_root(config.root)
        return TransportSpec(
            kind=self.kind,
            provider=config.provider,
            namespace=f"gdrive/{root}",
            options={
                "root": root,
                "client_id": config.client_id,
                "client_secret": config.client_secret,
                "refresh_token": config.refresh_token,
            },
        )

    def operator_options(self, spec: TransportSpec) -> dict[str, str]:
        options = spec.options
        return {
            "root": str(options["root"]),
            "client_id": str(options["client_id"]),
            "client_secret": str(options["client_secret"]),
            "refresh_token": str(options["refresh_token"]),
        }

    def target(self, options: Mapping[str, object]) -> StorageTargetIdentity:
        # Credentials are not a durable account identity. Conservatively group
        # all Drive targets until the authenticated account can be established.
        del options
        return StorageTargetIdentity(
            transport=self.kind.value, endpoint="https://www.googleapis.com"
        )

    def provider_domain(self, identity: StorageTargetIdentity) -> str | None:
        del identity
        return "provider:google"

    def provider_ref_fields(
        self,
        *,
        options: Mapping[str, object],
        backend: object,
        namespace: str,
    ) -> dict[str, object]:
        # The OAuth client id identifies the configured Google application but
        # grants no access by itself. Refresh/access tokens and the client
        # secret remain excluded from durable locators.
        del backend
        return {
            "gdrive": {
                "client_id": str(options.get("client_id") or "").strip(),
                "root": str(options.get("root") or namespace).strip().strip("/"),
            }
        }
