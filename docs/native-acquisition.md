# Native URL acquisition

PrintStash streams every server-side HTTP and HTTPS import body in Rust while
preserving the existing Python import coordinator, SSRF policy, capacity
admission, durable checkpoints, and public API. Direct URL imports, resolved
public-provider assets, and signed connected-provider downloads all converge on
this native byte path. M13 is complete for the revised migration scope described
in `rust-import-migration.md`.

## Ownership

| Concern | Owner |
| --- | --- |
| One-hop HTTP request, DNS pin use, redirect refusal, byte ceiling, streaming SHA-256, temporary spool, flush/sync, and create-only publication | Framework-free Rust `printstash-acquisition-core` crate |
| Request/result translation and asyncio integration | Narrow PyO3 adapter in `printstash-mesh-native` |
| URL normalization, DNS resolution, public-address policy, and validation of every redirect hop | Framework-independent `printstash-core` policy called by the Python coordinator |
| Redirect coordination, filename selection, capacity admission, and error translation | Python ingestion coordinator |
| Download checkpoints and staging identity receipts | Python `AcquisitionJournal` and `StagingLease` transactions |
| Durable job, Artifact publication, storage generation, and credentials | Existing Python application owners |

Provider API calls that rotate credentials or turn an authenticated selection
into a short-lived download URL remain in Python. They are authentication and
metadata coordination, not a second body downloader: the returned URL is passed
to the same native stream. Inbox enumeration likewise remains orchestration,
while browser capture uploads remain at the Python HTTP boundary. Portable
archive bytes are local inputs handled by the M09 native archive stage. External
LibrarySource materialization remains behind `StorageBackend`, whose M07 cutover
was explicitly deferred.

Rust receives a normalized URL and its already validated `(host, IP, port)`
tuple. It rejects mismatches and configures Reqwest to dial that IP while
retaining the URL host for the HTTP `Host` header, TLS SNI, and certificate
verification. Automatic redirects and proxies are disabled. Python resolves
and validates the next URL before each redirect request, so a redirect cannot
bypass the existing address policy.

The PyO3 adapter uses `pyo3-async-runtimes`' process-wide Tokio runtime.
HTTP, TLS, hashing, temporary ownership, and publication stay inside the
reusable core crate. The response body remains in a Rust-owned
`NamedTempFile`; Python receives only status/header metadata, selects the
established safe suffix, and asks the native result to publish into the same
directory with `persist_noclobber`.
Dropping, cancelling, rejecting, or failing before publication removes the
temporary file. Python never copies or rereads the response body, and the
streaming digest becomes the durable staging receipt.

## Dependencies

The direct dependencies were rechecked against their stable releases on
2026-09-19 and are exact in
`backend/rust/acquisition-core/Cargo.toml`, the root binding manifest, and
`Cargo.lock`:

| Dependency | Version | Purpose and selected features | License |
| --- | ---: | --- | --- |
| Reqwest | 0.13.5 | Established async HTTP client; redirects, proxy discovery, compression, and default TLS features disabled | MIT OR Apache-2.0 |
| Tokio | 1.53.1 | Native async file, socket, timer, and runtime support; no custom executor | MIT |
| pyo3-async-runtimes | 0.29.0 | Asyncio/Tokio bridge matching PyO3 0.29 | Apache-2.0 |
| Rustls | 0.23.45 | TLS 1.2/1.3 with the Ring provider; no OpenSSL or AWS-LC build dependency | Apache-2.0 OR ISC OR MIT |
| sha2 | 0.11.0 | Streaming SHA-256 receipt | MIT OR Apache-2.0 |
| tempfile | 3.27.0 | OS-backed temporary ownership and create-only persistence | MIT OR Apache-2.0 |

All support the repository Rust 1.88 toolchain. Reqwest requires Rust 1.85 and
`pyo3-async-runtimes` requires Rust 1.83. The application retains only policy
that these libraries do not supply: public-address admission, redirect-by-
redirect revalidation, capacity reservations, checkpoint fencing, safe display
names, and stable application errors.

## Preserved contracts

- HTTP and HTTPS only; credentials in URLs remain rejected before network I/O.
- Every DNS answer must pass the existing public-address policy. Rust connects
  only to the selected validated address and refuses an inconsistent URL tuple.
- Redirects are bounded by the existing setting, resolved relative to the
  normalized response URL, and revalidated before the next request.
- `Content-Encoding` is requested as `identity`; both declared and streamed
  bytes are bounded by `max_upload_bytes` without unbounded buffering.
- A successful body is flushed and synced before a create-only rename. Existing
  destination bytes are never replaced, and failed/oversized bodies leave no
  published or temporary staging file.
- The existing `Content-Disposition` and final-URL filename behavior determines
  the staging suffix and original filename.
- The journal stores the native streaming digest with the existing path identity
  receipt. Replay restores valid bytes without a second request and rejects a
  replaced staging object.
- No database, queue, HTTP response, command envelope, Artifact schema, storage
  provider, or credential format changes.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | validates the pinned target | Error/Edge | URL host/port differs, URL contains credentials, or an IPv6 literal uses bracketed/unbracketed equivalent forms | Mismatches fail before client creation; equivalent IPv6 forms are accepted | Rust | ✅ `rust/acquisition-core/src/lib.rs::tests::{rejects_a_target_that_does_not_match_the_url,rejects_credentials_and_invalid_limits,accepts_equivalent_ipv6_literal_forms}` |
| 2 | preserves create-only publication | Edge | Destination already contains bytes | Existing bytes remain intact; native publication fails | Rust | ✅ `rust/acquisition-core/src/lib.rs::tests::create_only_publication_preserves_an_existing_destination` |
| 3 | streams and hashes a real file | Happy | Loopback server, disposition filename, committed STL fixture | Exact bytes, suffix, filename, SHA-256, and staging parent | Contract | ✅ `tests/contract/api/v1/test_ingest.py::TestDownloadToStaging::test_download_to_staging_fetches_real_file` |
| 4 | revalidates redirects | Edge | Relative redirect to final STL | Final name and bytes returned after a separate validated hop | Contract | ✅ `TestDownloadToStaging::test_download_to_staging_follows_redirect` |
| 5 | rejects unsafe destinations | Error | Loopback target under the real SSRF policy | No request is admitted | Contract | ✅ `TestDownloadToStaging::test_download_to_staging_rejects_private_host` |
| 6 | bounds and cleans failed downloads | Error | Oversized body, missing redirect location, or HTTP 404 | Stable error and no partial staging file | Contract | ✅ `TestDownloadToStaging::{test_download_to_staging_enforces_size_limit,test_download_to_staging_rejects_redirect_without_location,test_download_to_staging_rejects_http_failure_without_partial_file}` |
| 7 | persists the streaming receipt | Happy/Replay | Native download result with digest, claimed durable command | Checkpoint and lease restore the exact staged object without hashing it again | Integration | ✅ `tests/integration/modules/ingestion/test_acquisition.py::TestAcquisitionContract::test_records_the_streaming_download_receipt_without_rereading` |
| 8 | imports the native download | Happy | Real loopback download through `/ingest/url` | Completed job, Model source URL, STL Artifact and exact size | Contract | ✅ `tests/contract/api/v1/test_ingest.py::TestIngestUrl::test_ingest_url_imports_a_real_download` |
| 9 | compares committed acquisition paths | Performance | Small, large, and redirected bodies | Exact filenames, sizes, hashes, request count, and transferred bytes match before timing | Repo | ✅ `tests/repo/test_bench_acquisition.py` |
| 10 | routes resolved provider assets through the shared downloader | Happy | V2 provider selection with a resolved URL | Asset staging calls `importer.download_to_staging` and retains source identity | Integration | ✅ `tests/integration/modules/ingestion/inbox/test_staging.py::TestDownloadResolvedAsset` |
| 11 | resolves connected-provider download URLs without exposing credentials | Edge | Owner-scoped provider connection and selected file | Only a short-lived URL crosses into asset staging; rotation remains isolated in the provider transaction | Integration | ✅ `tests/integration/modules/ingestion/test_import_resolvers.py`, `tests/integration/modules/ingestion/provider_connections/test_mmf_tokens.py` |

## Performance protocol

Protocol `native-acquisition-v1` runs the public `download_to_staging` seam from
the immediate parent and M00 baseline release images. A private loopback server
serves a small STL, an 8 MiB 3MF payload, and a redirect to that payload. The
quick correctness lane uses 256 KiB. Each measured run verifies filenames,
sizes, SHA-256 digests, four requests per iteration, and exact transferred bytes.

One warm-up precedes seven alternating pairs under 2 CPU/2 GiB and 4 CPU/4 GiB,
extended to fourteen when noisy. The report records workload p50/p95/max,
throughput, wall time, process CPU, sampled RSS, and container peak memory. The
database label is retained because the full matrix runs under both application
profiles; this isolated stage uses a disposable local capacity database and does
not attribute PostgreSQL service cost to HTTP streaming. The standard 5%
elapsed/latency and 10% CPU/memory review thresholds apply after correctness.

## Delivery and scope closeout

There is no schema or stored-data migration. Rollback deploys the parent image;
existing staging receipts and Artifacts remain readable. A missing or
incompatible native extension fails clearly rather than selecting the former
Python body streamer.

The original milestone text grouped byte acquisition with provider discovery,
credential handling, inbox enumeration, checkpoints, and external source
adapters. That ownership would contradict the accepted post-M01 boundary: Python
retains authentication and import coordination, and M03–M07 durable state and
storage were deferred. M13 therefore closes on the reusable
`printstash-acquisition-core` seam: Rust owns remote body transfer, bounding,
hashing, and create-only staging; Python owns policy and durable coordination.
There is no remaining Python HTTP body streamer in the import path.

Connection reuse is intentionally limited to one validated hop because a shared
client cannot silently retain host-to-IP mappings across a new DNS validation.
