# Storage providers

PrintStash probes the configured storage at startup. The expected tier below is guidance; `/api/v1/health` and Settings report the active probed tier.

| Provider | Category | Expected tier | Configuration fields |
| --- | --- | --- | --- |
| [This machine](#local) | This machine | Verified | `data_dir`, `thumb_dir`, `root` |
| [Amazon S3 or compatible](#s3) | S3-compatible object storage | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret), `endpoint_url` |
| [Cloudflare R2](#cloudflare_r2) | S3-compatible object storage | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret), `account_id` |
| [Backblaze B2](#backblaze_b2) | S3-compatible object storage | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret) |
| [Wasabi](#wasabi) | S3-compatible object storage | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret) |
| [Self-hosted S3](#s3_self_hosted) | S3-compatible object storage | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret), `endpoint_url` |
| [Nextcloud](#nextcloud) | Nextcloud and WebDAV | Unguarded | `endpoint_url`, `username`, `password` (secret), `root` |
| [WebDAV](#webdav) | Nextcloud and WebDAV | Unguarded | `endpoint_url`, `username`, `password` (secret), `root` |
| [SFTP](#sftp) | NAS over SFTP | Unguarded | `host`, `port`, `username`, `private_key_path`, `root` |

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

Expected tier: **Unguarded**. Remote rename does not prove conditional ownership.

## webdav

Remote storage over WebDAV.

Expected tier: **Unguarded**. Remote rename does not prove conditional ownership.

## sftp

NAS storage over SSH File Transfer Protocol.

Expected tier: **Unguarded**. SFTP cannot prove conditional ownership.

## Credentials and upgrades

Secrets are write-only: configuration reads expose only which secret fields are set. SFTP uses a mounted, unencrypted service-key path; inline private-key material is rejected. The current transport does not support password authentication or encrypted keys.

PrintStash never creates an S3 bucket or changes its lifecycle policy. Grant data-plane access plus read-only bucket/versioning/lifecycle inspection; remove `s3:CreateBucket` and `s3:PutLifecycleConfiguration` from older policies.

`VAULT_STORAGE_BACKEND` and the legacy S3 variables remain compatibility inputs. New deployments should use `VAULT_STORAGE_PROVIDER` and `VAULT_STORAGE_PROVIDER_CONFIG`.
