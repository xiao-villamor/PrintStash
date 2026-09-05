# Storage providers

This page configures **managed Vault storage**, where PrintStash owns object
creation and cleanup. To index files already owned by a NAS, S3 bucket, WebDAV
collection or SFTP directory, use a read-only
[Library source](./library-sources.md). Reusing the same server does not merge
the two ownership domains.

PrintStash probes the configured storage at startup. Support maturity and storage safety are separate: the expected tier below is guidance, while `/api/v1/health` and Settings report the measured active tier.

| Provider | Category | Support | Expected tier | Configuration fields |
| --- | --- | --- | --- | --- |
| [This machine](#local) | This machine | Stable | Verified | `data_dir`, `thumb_dir`, `root` |
| [Amazon S3 or compatible](#s3) | S3-compatible object storage | Stable | Guarded | `bucket`, `region`, `addressing_style`, `root`, `access_key` (secret), `secret_key` (secret), `endpoint_url` |
| [Cloudflare R2](#cloudflare_r2) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `addressing_style`, `root`, `access_key` (secret), `secret_key` (secret), `account_id` |
| [Backblaze B2](#backblaze_b2) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `addressing_style`, `root`, `access_key` (secret), `secret_key` (secret) |
| [Wasabi](#wasabi) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `addressing_style`, `root`, `access_key` (secret), `secret_key` (secret) |
| [Self-hosted S3](#s3_self_hosted) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `addressing_style`, `root`, `access_key` (secret), `secret_key` (secret), `endpoint_url` |
| [Nextcloud](#nextcloud) | Nextcloud and WebDAV | Beta | Guarded | `endpoint_url`, `username`, `password` (secret), `root` |
| [WebDAV](#webdav) | Nextcloud and WebDAV | Beta | Guarded | `endpoint_url`, `username`, `password` (secret), `root` |
| [SFTP](#sftp) | NAS over SFTP | Beta | Guarded | `host`, `host_key`, `port`, `username`, `password` (secret), `private_key_path`, `passphrase` (secret), `root` |
| [Google Drive](#gdrive) | Consumer cloud storage | Beta | Unguarded | `client_id`, `client_secret` (secret), `refresh_token` (secret), `root` |

## Safety tiers

- **Verified** storage proves conditional creation, replacement identity, and deletion identity. Automated storage-backed purge is allowed.
- **Guarded** storage proves unique creation but lacks at least one destructive-operation proof. Manual permanent deletion requires one-shot confirmation; scheduled storage purge is skipped.
- **Unguarded** storage cannot prove unique creation. Startup additionally requires `VAULT_STORAGE_ALLOW_UNVERIFIED=true`.

Directory `fsync` support is diagnostic only. Local paths on network or unknown filesystems are capped at Guarded even when hardlinks work.

## local

Local filesystem directories.

Expected tier: **Verified**. Verified on local filesystems with working hardlinks.

## s3

Native S3-compatible object storage.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

Use the concrete AWS region for Amazon S3. Leave `endpoint_url` empty and keep
`addressing_style=auto` unless the account has a specific endpoint requirement.
For self-hosted S3, `addressing_style=auto` resolves to path style because many
NAS and local object stores do not provide wildcard bucket DNS. Select
`virtual` only when the endpoint, DNS and TLS certificate support virtual-host
bucket names.

The startup probe creates and cleans up a unique probe object. When the server
returns a VersionId, cleanup targets that exact version. It never deletes a
same-key replacement by an external writer.

## cloudflare_r2

Native S3-compatible object storage.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

## backblaze_b2

Native S3-compatible object storage.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

## wasabi

Native S3-compatible object storage.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

## s3_self_hosted

Native S3-compatible object storage.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

## nextcloud

Remote storage over WebDAV.

Expected tier: **Guarded**. Confirmed catalog removal retains stored bytes; exact physical deletion is unavailable.

## webdav

Remote storage over WebDAV.

Expected tier: **Guarded**. Confirmed catalog removal retains stored bytes; exact physical deletion is unavailable.

## sftp

NAS storage over SSH File Transfer Protocol.

Expected tier: **Guarded**. Publish uses SSH exclusive create (`x` mode); `host_key` is required and purge is manual and confirmed only.

## gdrive

Consumer cloud storage through Apache OpenDAL.

Expected tier: **Unguarded**. Available for read-only Library sources and off-site backup replicas; not selectable as managed Vault storage.

## Credentials and upgrades

Secrets are write-only: configuration reads expose only which secret fields are set. SFTP accepts exactly one authentication mode: password, or a mounted private-key path with an optional passphrase. Inline private-key material is rejected. New and updated SFTP configurations require `host_key` as either a mounted known-hosts path or an OpenSSH known-host entry; legacy rows without it remain readable but cannot activate until it is added.

PrintStash never creates an S3 bucket or changes its lifecycle policy. Grant data-plane access plus read-only bucket/versioning/lifecycle inspection; remove `s3:CreateBucket` and `s3:PutLifecycleConfiguration` from older policies.

New deployments should select and save a provider through Setup or Settings.
Environment-only deployments use scalar fields: `VAULT_STORAGE_PROVIDER` and
`VAULT_STORAGE_ROOT`, plus `VAULT_S3_*`, `VAULT_WEBDAV_*`, or `VAULT_SFTP_*`
for the selected transport. `VAULT_STORAGE_PROVIDER_CONFIG` and
`VAULT_STORAGE_PROVIDER_SECRETS` remain compatibility inputs but are deprecated.

The checked-in Compose files forward the legacy/local and `VAULT_S3_*` fields,
but do not automatically forward `VAULT_STORAGE_PROVIDER`,
`VAULT_STORAGE_ROOT`, `VAULT_WEBDAV_*`, `VAULT_SFTP_*`, or
`VAULT_STORAGE_ALLOW_UNVERIFIED` from `.env`. When configuring those fields
entirely through environment variables, add them explicitly under the API
service's `environment` in a Compose override. Configuration saved through the
Setup or Settings UI does not need that override.

`VAULT_STORAGE_BACKEND` and the legacy S3 variables remain supported upgrade
inputs. Keep them unchanged for the first 0.13.0 compatibility boot.

Changing from the legacy `s3` input to a typed provider does not move bytes.
Adopt only an equivalent bucket, endpoint, region, addressing style and root.
There is no general provider-to-provider byte migration in 0.13.0.
