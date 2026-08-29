# Storage providers

PrintStash probes the configured storage at startup. Support maturity and storage safety are separate: the expected tier below is guidance, while `/api/v1/health` and Settings report the measured active tier.

| Provider | Category | Support | Expected tier | Configuration fields |
| --- | --- | --- | --- | --- |
| [This machine](#local) | This machine | Stable | Verified | `data_dir`, `thumb_dir`, `root` |
| [Amazon S3 or compatible](#s3) | S3-compatible object storage | Stable | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret), `endpoint_url` |
| [Cloudflare R2](#cloudflare_r2) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret), `account_id` |
| [Backblaze B2](#backblaze_b2) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret) |
| [Wasabi](#wasabi) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret) |
| [Self-hosted S3](#s3_self_hosted) | S3-compatible object storage | Beta | Guarded | `bucket`, `region`, `root`, `access_key` (secret), `secret_key` (secret), `endpoint_url` |
| [Nextcloud](#nextcloud) | Nextcloud and WebDAV | Beta | Guarded | `endpoint_url`, `username`, `password` (secret), `root` |
| [WebDAV](#webdav) | Nextcloud and WebDAV | Beta | Guarded | `endpoint_url`, `username`, `password` (secret), `root` |
| [SFTP](#sftp) | NAS over SFTP | Beta | Guarded | `host`, `host_key`, `port`, `username`, `password` (secret), `private_key_path`, `passphrase` (secret), `root` |

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

Expected tier: **Guarded**. Publish uses WebDAV MOVE with `Overwrite: F`; purge is manual and confirmed only.

## webdav

Remote storage over WebDAV.

Expected tier: **Guarded**. Publish uses WebDAV MOVE with `Overwrite: F`; purge is manual and confirmed only.

## sftp

NAS storage over SSH File Transfer Protocol.

Expected tier: **Guarded**. Publish uses SSH exclusive create (`x` mode); `host_key` is required and purge is manual and confirmed only.

## Credentials and upgrades

Secrets are write-only: configuration reads expose only which secret fields are set. SFTP accepts exactly one authentication mode: password, or a mounted private-key path with an optional passphrase. Inline private-key material is rejected. New and updated SFTP configurations require `host_key` as either a mounted known-hosts path or an OpenSSH known-host entry; legacy rows without it remain readable but cannot activate until it is added.

PrintStash never creates an S3 bucket or changes its lifecycle policy. Grant data-plane access plus read-only bucket/versioning/lifecycle inspection; remove `s3:CreateBucket` and `s3:PutLifecycleConfiguration` from older policies.

`VAULT_STORAGE_BACKEND` and the legacy S3 variables remain compatibility inputs. New deployments should use `VAULT_STORAGE_PROVIDER` and `VAULT_STORAGE_PROVIDER_CONFIG`.
