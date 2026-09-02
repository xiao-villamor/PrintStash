# Vault Maintenance, Capture, And Model Sources

Settings → Maintenance provides two read-only audit modes. Quick checks owned
blob reachability/size, external links, thumbnails, Metadata, Revision
invariants, background work, and unclaimed storage. Full also streams SHA-256
for owned primary blobs, checks embedded-image references, and verifies the
available backup archives. Findings expose safe labels and identifiers rather
than server paths or signed URLs. Repairs are explicit, idempotent, and written
to AuditLog.

## Pending Imports

Pending Imports (`/inbox`) stores a capture before any Model or Artifact exists.
Paste a supported URL or use the browser extension, then review its title,
selected files, destination Collection, and tags before adding anything to the
Vault. Final import rechecks Collection access and uses normal ingestion, whose
`persist_artifact` service remains the sole owner of Model, Artifact, Metadata,
and thumbnail persistence.

An import can finish completely, partially, or fail. Review the result rows to
see which files entered the Vault. Retry is deliberately limited to failed or
partial captures; a completed capture is not downloaded again just because it
is reopened. Dismissing an item removes its managed staging data but never an
already imported Model or Artifact.

Browser-transferred bytes use managed staging only until import. If that staging
file has expired or has been removed, PrintStash reports the stable
`staging_expired` error instead of guessing at a replacement; capture the file
again from the source page. Provider and resolver failures likewise remain
stable, safe codes (for example `makerworld_extension_required`,
`thingiverse_extension_required`, `provider_auth_failed`, and
`provider_retry_exhausted`) rather than upstream URLs, response bodies, or
credentials.

## Browser extension and supported sources

The Chrome/Firefox importer lives in `browser-extension/`. Build and load it,
then create a one-time pairing code in **Settings → Imports** and enter it with
the Vault URL and a device name. It verifies the PrintStash service and
authenticated user before exchanging the code for a browser-only credential,
rechecks the connection when opened, and removes that credential and the Vault
host permission on disconnect. Existing username/API-key setups remain a legacy
migration path; new setups should use pairing. Every capture remains reviewable
in Pending Imports before ingestion.

| Source | Capture path and current boundary |
| --- | --- |
| Printables | Server resolution can request the limited fields and file choices exposed by Printables. It cannot promise every field visible in a signed-in or changing page; the extension can provide richer visible-page capture when needed. |
| MakerWorld | Browser transfer only. The signed-in browser downloads the selected package and uploads its bytes to PrintStash; source-site cookies and credentials are not sent to PrintStash. |
| Thingiverse | Browser/manual file capture only. The server does not resolve Thingiverse ZIP downloads; the extension can transfer the official model ZIP from the active browser session, with manual attachment as the fallback. |
| MyMiniFactory | An official OAuth connection can obtain supported metadata and files through the provider API. |
| Cults | A credential connection is used for supported metadata only. PrintStash does not automatically acquire Cults files. |
| Direct files and safe archives | Normal URL/archive capture remains available where the resolver can safely obtain the file. |

The browser helper is not a general-purpose scraper. It captures only the
allowlisted page information and files that the user explicitly transfers.
Resolver requests are SSRF-guarded and provider traffic is bounded: requests
use approved public endpoints, limited redirects and retries, and bounded
concurrency. Capture code does not log provider payloads or credentials.

## Provider connections and browser pairing

Provider connections are per user. In Settings, connect MyMiniFactory through
its OAuth authorization flow, or enter Cults credentials for metadata lookups.
Disconnecting removes that connection. OAuth states are short-lived and
single-use; the provider application credentials are deployment configuration,
not user records.

To pair a browser, create a pairing code in Settings and claim it in the
extension with a device name. Pairing codes expire after five minutes, are
single-use, and lock after five failed exchange attempts. A user can have up to
10 active paired browsers. Settings lists devices so they can be renamed or
revoked; revocation stops that browser from using its pairing credential.

## Model Source and portable provenance

Models imported from a capture expose a **Source** tab. It records the canonical
source URL, provider identity, source item/revision when supplied, capture and
check dates, captured fields, and snapshot history. Captured fields distinguish
confirmed values from inferred ones. A user can override an individual field;
that override takes precedence until it is explicitly cleared, at which point
the captured value becomes effective again.

Portable library exports include an optional `provenance.json` sidecar for
captured Artifact provenance. It is additive and optional: archives produced
before the sidecar remain importable. On import PrintStash validates the
sidecar before writes and retains an existing local user override when it
conflicts with an imported override.

## Privacy and retained data

Capture records keep the allowlisted source metadata needed for review and
provenance, not a replay of the source site. They do not retain raw HTML,
source-site cookies, OAuth authorization codes, signed download URLs, resolved
download credentials, or staging paths. URL capture strips secret-shaped query
parameters and rejects embedded URL credentials. Provider tokens and credentials
needed by an active provider connection are encrypted at rest; browser pairing
credentials are hashed. Neither is returned by Settings, diagnostics, audit
diffs, or capture errors.

The model browser supports URL-restorable filters for Artifact type, material,
slicer, printer model, Revision status, printed state, print outcome, vault or
external storage, and upload date. Values within one group use OR; different
groups use AND; repeated tags retain AND behavior. Saved Views store the same
contract, so old views continue to load and new views restore all active filters.
