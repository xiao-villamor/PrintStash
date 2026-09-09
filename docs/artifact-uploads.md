# Resumable Artifact uploads

PrintStash uses one provider-neutral upload session for browser model, G-code,
and revision uploads. The server chooses the transfer mode from the active
storage backend's proven capabilities:

- `native_parts` sends large parts directly from the browser to an
  S3-compatible staging object. The API issues short-lived, operation-scoped
  instructions and records receipts, but does not proxy object bytes.
- `api_chunks` sends fixed-size resumable chunks through PrintStash. It is the
  fallback for local storage, WebDAV, SFTP, and S3-compatible endpoints that do
  not prove the required checksum, listing, signing, and cleanup behavior.

Clients should create a session, request its current plan, transfer only the
missing parts, and finalize it. After an interruption, retain only the opaque
PrintStash session ID and request a fresh status and plan. Do not persist signed
URLs or native upload receipts. `DELETE /api/v1/artifact-uploads/{id}` cancels a
session and is idempotent.

Simple integrations such as slicer hooks can use `api_chunks` serially: upload
each part in index order with its byte offset, length, and SHA-256, then call
`finalize`. They do not need browser storage or provider-specific knowledge.

## Limits and recovery

`VAULT_MAX_UPLOAD_MB` limits one file. Staging admission also enforces
`VAULT_STAGING_MAX_PENDING`, `VAULT_STAGING_MAX_ACTIVE_PER_USER`,
`VAULT_STAGING_MAX_GB`, and `VAULT_STAGING_MIN_FREE_GB`. The API bounds chunk
bodies and rate-limits session creation, plan refresh, part/chunk recording, and
finalization.

Session state and part receipts are durable. Startup and periodic reconciliation
expire abandoned work, abort only positively owned native operations, and report
uncertain cleanup as a retryable safe failure. Verified staging is retained until
normal Artifact persistence succeeds or an explicitly owned abort removes it.

## S3 bucket CORS

Direct browser uploads require CORS on the bucket itself; PrintStash does not
modify bucket policy or CORS configuration. Add every exact browser origin that
serves the PrintStash frontend. Do not use `*` for an authenticated deployment.

An AWS-style bucket rule is:

```json
[
  {
    "AllowedOrigins": ["https://printstash.example.com"],
    "AllowedMethods": ["PUT"],
    "AllowedHeaders": ["content-type", "x-amz-checksum-sha256"],
    "ExposeHeaders": ["ETag", "x-amz-checksum-sha256"],
    "MaxAgeSeconds": 300
  }
]
```

Configure `VAULT_CORS_ORIGINS` with the same exact frontend origins so browser
requests to the PrintStash API are also restricted correctly. If the endpoint
cannot prove native multipart checksum or cleanup semantics, PrintStash safely
uses `api_chunks`; missing bucket CORS will otherwise appear as a browser
transfer failure even when server-side S3 access succeeds.

## Operator checks

Confirm that an interrupted upload reports its received parts after an API
restart, finalizes to the expected Model, and leaves no incomplete multipart
operation after cancellation or expiry. For S3-compatible services, repeat this
against each advertised target because checksum and CORS support vary between
implementations.
