# Storage providers

This page configures **managed Vault storage**, where PrintStash owns object
creation and cleanup. To index files already owned by a NAS, S3 bucket, WebDAV
collection or SFTP directory, use a read-only
[Library source](./library-sources.md). Reusing the same server does not merge
the two ownership domains.

PrintStash probes the configured storage at startup. Support maturity and storage safety are separate: the expected tier below is guidance, while `/api/v1/health` and Settings report the measured active tier.

| Provider | Transport | Vault | Library source | Backup destination | Runtime | Support | Expected tier | Browser delivery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [This machine](#local) | local | ✓ | ✓ | ✓ | All images | Stable | Verified | Proxy |
| [Amazon S3 or compatible](#s3) | s3 | ✓ | ✓ | ✓ | All images | Stable | Guarded | Signed GET candidate; proxy fallback |
| [Cloudflare R2](#cloudflare_r2) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Backblaze B2](#backblaze_b2) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Wasabi](#wasabi) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Self-hosted S3](#s3_self_hosted) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Nextcloud](#nextcloud) | webdav | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [WebDAV](#webdav) | webdav | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [SFTP](#sftp) | sftp | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [Google Drive](#gdrive) | gdrive | — | ✓ | ✓ | Full image | Beta | Unguarded | Proxy |
| [Synology — mounted folder](#synology) | local | ✓ | ✓ | ✓ | All images | Beta | Guarded | Proxy |
| [TrueNAS — mounted folder](#truenas) | local | ✓ | ✓ | ✓ | All images | Beta | Guarded | Proxy |
| [QNAP — mounted folder](#qnap) | local | ✓ | ✓ | ✓ | All images | Beta | Guarded | Proxy |
| [Unraid — mounted folder](#unraid) | local | ✓ | ✓ | ✓ | All images | Beta | Guarded | Proxy |
| [Synology — WebDAV](#synology_webdav) | webdav | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [QNAP — WebDAV](#qnap_webdav) | webdav | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [MinIO](#minio) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Garage](#garage) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [SeaweedFS](#seaweedfs) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Hetzner Object Storage](#hetzner_object_storage) | s3 | ✓ | ✓ | ✓ | All images | Beta | Guarded | Signed GET candidate; proxy fallback |
| [Hetzner Storage Box — SFTP](#hetzner_storage_box) | sftp | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [Hetzner Storage Box — WebDAV](#hetzner_storage_box_webdav) | webdav | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |
| [Koofr — WebDAV](#koofr) | webdav | ✓ | ✓ | ✓ | Full image | Beta | Guarded | Proxy |

## Delivery and role setup

S3 transports can offer signed browser GETs only when the configured endpoint proves safe URLs and CORS for the requesting origin. Every transport retains a same-origin fallback. Delivery capability is independent from deletion safety; a Guarded provider can support signed GET without permitting automatic deletion.

Mounted NAS presets configure Vault directories. For existing NAS files choose a mounted Library source; for backup folders choose a mounted destination. Mount SMB/NFS outside PrintStash and pass the mount into the container. No direct SMB adapter is included. WebDAV and SFTP are explicit alternative entries, not automatic protocol detection.

## Safety tiers

- **Verified** storage proves conditional creation, replacement identity, and deletion identity. Automated storage-backed purge is allowed.
- **Guarded** storage proves unique creation but lacks at least one destructive-operation proof. Manual permanent deletion requires one-shot confirmation; scheduled storage purge is skipped.
- **Unguarded** storage cannot prove unique creation. Startup additionally requires `VAULT_STORAGE_ALLOW_UNVERIFIED=true`.

Directory `fsync` support is diagnostic only. Local paths on network or unknown filesystems are capped at Guarded even when hardlinks work.

## local

Local filesystem directories.

Transport: **local**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `data_dir`, `thumb_dir`.

Large objects: bounded filesystem streaming and range reads.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Verified**. Verified on local filesystems with working hardlinks.

Known limitations: Verified on local filesystems with working hardlinks.

## s3

Native S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

Known limitations: Verified when bucket versioning is enabled; otherwise Guarded.

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

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `account_id`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

Known limitations: Verified when bucket versioning is enabled; otherwise Guarded.

## backblaze_b2

Native S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

Known limitations: Verified when bucket versioning is enabled; otherwise Guarded.

## wasabi

Native S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

Known limitations: Verified when bucket versioning is enabled; otherwise Guarded.

## s3_self_hosted

Native S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `endpoint_url`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Verified when bucket versioning is enabled; otherwise Guarded.

Known limitations: Verified when bucket versioning is enabled; otherwise Guarded.

## nextcloud

Remote storage over WebDAV.

Transport: **webdav**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with WebDAV support.

Configuration prerequisites: `root`, `endpoint_url`, `username`, `password` (write-only secret).

Large objects: bounded streaming/materialization with proxy delivery; provider limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Confirmed catalog removal retains stored bytes; exact physical deletion is unavailable.

Known limitations: Confirmed catalog removal retains stored bytes; exact physical deletion is unavailable.

## webdav

Remote storage over WebDAV.

Transport: **webdav**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with WebDAV support.

Configuration prerequisites: `root`, `endpoint_url`, `username`, `password` (write-only secret).

Large objects: bounded streaming/materialization with proxy delivery; provider limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Confirmed catalog removal retains stored bytes; exact physical deletion is unavailable.

Known limitations: Confirmed catalog removal retains stored bytes; exact physical deletion is unavailable.

## sftp

NAS storage over SSH File Transfer Protocol.

Transport: **sftp**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires AsyncSSH.

Configuration prerequisites: `root`, `host`, `username`, `host_key`.

Large objects: bounded streaming/materialization with proxy delivery; server limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. Publish uses SSH exclusive create (`x` mode); `host_key` is required and confirmed catalog purge retains stored bytes.

Known limitations: Publish uses SSH exclusive create (`x` mode); `host_key` is required and confirmed catalog purge retains stored bytes.

## gdrive

Consumer cloud storage through Apache OpenDAL.

Transport: **gdrive**.

Supported roles: Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with Google Drive support.

Configuration prerequisites: `root`, `client_id`, `client_secret` (write-only secret), `refresh_token` (write-only secret).

Large objects: bounded materialization/readback with proxy delivery; provider quotas and throttling apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Unguarded**. Available for read-only Library sources and off-site backup replicas; not selectable as managed Vault storage.

Known limitations: Available for read-only Library sources and off-site backup replicas; not selectable as managed Vault storage.

## synology

NAS folder mounted on this host.

Transport: **local**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `data_dir`, `thumb_dir`.

Large objects: bounded filesystem streaming and range reads.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Mount an SMB/NFS share on the PrintStash host, then bind it into the container. Enter container paths, not a NAS URL. Existing models belong in a mounted Library source; backup folders use the mounted backup destination.

[Provider instructions](https://kb.synology.com/en-global/DSM/tutorial/How_to_access_files_on_Synology_NAS_within_the_local_network_NFS)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## truenas

NAS folder mounted on this host.

Transport: **local**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `data_dir`, `thumb_dir`.

Large objects: bounded filesystem streaming and range reads.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Create an SMB or NFS share, mount it on the PrintStash host, and bind it into the container. Enter container paths. Existing models use a mounted Library source; backup folders use the mounted backup destination.

[Provider instructions](https://www.truenas.com/docs/scale/shares/nfs/addingnfsshares/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## qnap

NAS folder mounted on this host.

Transport: **local**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `data_dir`, `thumb_dir`.

Large objects: bounded filesystem streaming and range reads.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Mount the NAS share on the PrintStash host and bind it into the container. Enter container paths. Existing models use a mounted Library source; backup folders use the mounted backup destination. WebDAV is a separate connection choice.

[Provider instructions](https://www.qnap.com/en-us/how-to/tutorial/article/accessing-your-qnap-nas-remotely-with-webdav)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## unraid

NAS folder mounted on this host.

Transport: **local**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `data_dir`, `thumb_dir`.

Large objects: bounded filesystem streaming and range reads.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enable the share's SMB or NFS export and mount it on the PrintStash host, or bind a host share directory into the container. Existing models use a mounted Library source; backup folders use the mounted backup destination.

[Provider instructions](https://docs.unraid.net/unraid-os/using-unraid-to/manage-storage/shares/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## synology_webdav

Remote storage over WebDAV.

Transport: **webdav**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with WebDAV support.

Configuration prerequisites: `root`, `endpoint_url`, `username`, `password` (write-only secret).

Large objects: bounded streaming/materialization with proxy delivery; provider limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Install and enable WebDAV Server. Enter your HTTPS WebDAV endpoint (default HTTPS port 5006), including the shared folder. Use a NAS account granted access to that folder; reverse proxy ports can differ.

[Provider instructions](https://kb.synology.com/en-sg/DSM/tutorial/How_to_fix_WebDAV_connection_issues)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## qnap_webdav

Remote storage over WebDAV.

Transport: **webdav**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with WebDAV support.

Configuration prerequisites: `root`, `endpoint_url`, `username`, `password` (write-only secret).

Large objects: bounded streaming/materialization with proxy delivery; provider limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enable WebDAV and the shared folder's WebDAV permissions. Copy the HTTPS endpoint and configured port from QTS, including the shared folder. Use an account with access to that folder; no universal NAS endpoint is assumed.

[Provider instructions](https://www.qnap.com/en-us/how-to/tutorial/article/accessing-your-qnap-nas-remotely-with-webdav)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## minio

S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `endpoint_url`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enter the S3 API endpoint, usually port 9000, not the console. Use a bucket-scoped access key and secret key. Region must match the server configuration; path-style addressing is the preset default.

[Provider instructions](https://github.com/minio/minio)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## garage

S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `endpoint_url`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enter the S3 API endpoint, usually port 3900, and the configured s3_region (commonly garage). Grant the access key access to the existing bucket. Path-style addressing is the preset default.

[Provider instructions](https://garagehq.deuxfleurs.fr/documentation/connect/cli/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## seaweedfs

S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `endpoint_url`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enter the S3 gateway endpoint, usually port 8333, with access and secret keys configured on the gateway. Configure authentication before exposing the service. Path-style addressing is the preset default.

[Provider instructions](https://github.com/seaweedfs/seaweedfs/wiki/Amazon-S3-API)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## hetzner_object_storage

S3-compatible object storage.

Transport: **s3**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **All images**; uses a built-in transport.

Configuration prerequisites: `root`, `bucket`, `access_key` (write-only secret), `secret_key` (write-only secret).

Large objects: multipart or bounded streaming writes and range reads.

Browser delivery: Signed GET candidate; proxy fallback. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Choose the bucket location as the signing region (for example fsn1, nbg1 or hel1). The endpoint is https://REGION.your-objectstorage.com unless explicitly overridden. Use Object Storage access and secret keys, not Storage Box credentials.

[Provider instructions](https://docs.hetzner.com/storage/object-storage/getting-started/using-libraries/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## hetzner_storage_box

Remote storage over SFTP.

Transport: **sftp**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires AsyncSSH.

Configuration prerequisites: `root`, `host`, `username`, `host_key`.

Large objects: bounded streaming/materialization with proxy delivery; server limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enable SSH support for port 23, then enter the exact hostname and username from the Storage Box account or sub-account. Verify and pin its SSH host key out of band. Use the account password or a mounted private key. SFTP on port 22 can be selected explicitly.

[Provider instructions](https://docs.hetzner.com/storage/storage-box/access/access-overview/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## hetzner_storage_box_webdav

Remote storage over WebDAV.

Transport: **webdav**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with WebDAV support.

Configuration prerequisites: `root`, `endpoint_url`, `username`, `password` (write-only secret).

Large objects: bounded streaming/materialization with proxy delivery; provider limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Enable WebDAV, then enter https://HOSTNAME using the hostname and username assigned to the account or sub-account. HTTPS uses port 443. Use the Storage Box password and a dedicated folder.

[Provider instructions](https://docs.hetzner.com/storage/storage-box/access/access-overview/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## koofr

Remote storage over WebDAV.

Transport: **webdav**.

Supported roles: Vault, Library source, Backup destination.

Runtime packaging: **Full image**; requires OpenDAL with WebDAV support.

Configuration prerequisites: `root`, `endpoint_url`, `username`, `password` (write-only secret).

Large objects: bounded streaming/materialization with proxy delivery; provider limits apply.

Browser delivery: Proxy. Signed targets are never returned by catalogue metadata.

Expected tier: **Guarded**. The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Known limitations: The endpoint probe determines safety. A preset does not certify hardware or authorize deletion.

Use your Koofr account email as username and generate an application-specific password for WebDAV. The endpoint is case-sensitive. The base folder is relative to the Koofr endpoint; do not repeat dav/Koofr in it.

[Provider instructions](https://koofr.eu/help/koofr_with_webdav/how-do-i-connect-a-service-to-koofr-through-webdav/)

Evidence: transport contracts only; this is not hardware or hosted-account certification. Validate the endpoint and each intended role before use.

## Credentials and upgrades

Secrets are write-only: configuration reads expose only which secret fields are set. SFTP accepts exactly one authentication mode: password, or a mounted private-key path with an optional passphrase. Inline private-key material is rejected. New and updated SFTP configurations require `host_key` as either a mounted known-hosts path or an OpenSSH known-host entry; legacy rows without it remain readable but cannot activate until it is added.

PrintStash never creates an S3 bucket or changes its lifecycle policy. Grant data-plane access plus read-only bucket/versioning/lifecycle inspection; remove `s3:CreateBucket` and `s3:PutLifecycleConfiguration` from older policies.

Large direct browser uploads also require bucket CORS for `PUT`, the
`content-type` and `x-amz-checksum-sha256` request headers, and the exposed
`ETag` response header. Use only the exact configured frontend origins. See
[Resumable Artifact uploads](./artifact-uploads.md#s3-bucket-cors) for the rule
and fallback behavior.

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
