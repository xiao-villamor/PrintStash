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

The Chrome/Edge/Firefox importer lives in `browser-extension/`. Build and load it,
then create a one-time pairing code in **Settings → Imports** and enter it with
the Vault URL and a device name. It verifies the PrintStash service and
authenticated user before exchanging the code for a browser-only credential,
rechecks the connection when opened, and removes that credential and the Vault
host permission on disconnect. Existing username/API-key setups remain a legacy
migration path; new setups should use pairing.

On a model page, open PrintStash and choose **Import**. PrintStash queues the
import and the extension shows **Open in PrintStash** when it finishes. Pending
Imports retains the receipt. If a capture needs a file or destination decision,
the extension opens the review flow. Turn on **Review before importing** in
extension Settings to send captures to Pending Imports for review by default.
The extension remembers your last collection and tags for that Vault account.
An existing capture offers **Open** before you send the same source again.
PrintStash selects a sole file or package, a profile named by the page, or the
publication’s model files. Use **Change** to pick different files. If the page
lists competing packages without a clear default, choose one before importing.
Publication images are included by default. Select a thumbnail to set the
cover, or use **Don’t include images** to omit the gallery.
Connecting with **Allow supported marketplaces** on grants the server and every
supported marketplace in one Chrome prompt; imports then never ask. With it
off, the first import from a marketplace asks once, at the click, for all of
that marketplace's hosts. If Chrome closes the toolbar popup after granting
access, reopen it to continue.

The extension reaches servers on the LAN (including `.lan`, `.home.arpa`,
`.internal` names and Tailscale addresses), behind HTTPS reverse proxies, and
behind sign-in proxies such as Cloudflare Access or Authelia once you sign in
to them in a normal tab. Plain HTTP to a server outside the LAN needs explicit
confirmation. Uploads go in parts, so a proxy's request-size limit does not
cap file size; see the extension README for details.

| Source | Capture path and current boundary |
| --- | --- |
| Printables | Server resolution can request the limited fields and file choices exposed by Printables. It cannot promise every field visible in a signed-in or changing page; the extension can provide richer visible-page capture when needed. |
| MakerWorld | Browser transfer only. The signed-in browser downloads the selected package and uploads its bytes to PrintStash; source-site cookies and credentials are not sent to PrintStash. |
| Thingiverse | Browser/manual file capture only. The server does not resolve Thingiverse downloads; the extension can list and transfer selected individual files from the active page, with manual attachment as the fallback. |
| MyMiniFactory | The OAuth connection supports metadata and files. The browser panel also selects native file downloads; the observed `dl4.myminifactory.com` CDN is allowed. |
| Cults | The browser panel resolves native free-download forms and transfers the selected archive. It does not submit paid checkout. The server credential connection remains metadata-only. |
| Thangs | The signed-in browser transfers the selected native archive; supported meshes are reviewed before import. |
| Creality Cloud | The browser selects print-profile packages from the observed USA download region. Other download regions are not yet validated. |
| MakerOnline | The browser selects a profile package. Complete any source-site slider or verification manually; PrintStash does not solve it. |
| Direct files and safe archives | Normal URL/archive capture remains available where the resolver can safely obtain the file. |

The browser helper is not a general-purpose scraper. It captures only the
allowlisted page information and files that the user explicitly transfers.
Resolver requests are SSRF-guarded and provider traffic is bounded: requests
use approved public endpoints, limited redirects and retries, and bounded
concurrency. Capture code does not log provider payloads or credentials.

## Recovering individual browser captures

Choose **Enable panel on this site** in the extension's **Capture** tab to turn
on the launcher for a supported marketplace. **Settings** lets you select the
launcher's style and corner or hide it globally or just for the current site.
Use toolbar **Settings** to show it again. The **History** tab lists previous
captures of the current publication.

Hiding the panel keeps the transfer running; reloading the page requires recovery. With a compatible
server, reselecting the same files recovers verified uploads until their staging
leases expire. Provider metadata may have changed in the meantime: recovery keeps
the original capture snapshot. It never persists signed download links or model
bytes in extension storage.

Transfer completion leads to that capture's review page. A partial import shows
all results and file-specific errors. Retry retains successful results; expired
staging instead requires returning to the source for fresh bytes. Collection
selection organizes Models, while browser captures use managed Vault storage.

Provider profiles can carry explicit provider estimates and creator/printer
information. Review and Model details distinguish their source from measured
print outcomes. **Profiles from this publication** links only readable Models and
the exact original Artifact. Original source terms and user corrections remain
separate in the Source tab and portable provenance.

### Settings saved inside a 3MF

The Model overview shows **3MF project settings** for the selected Artifact.
A background derivative reads a bounded set of Bambu/Orca-style project fields:
profile name, printer model, materials, nozzle diameters, layer height, walls and
infill. These describe the saved project; individual objects can override them.
The existing plate viewer continues to own plate selection and preview geometry.

When the file contains slice metadata, its time and filament estimates are shown
per plate under **Estimate included in the file**. Missing values stay unavailable.
This evidence stays separate from **Provider estimate**, print history and
Recommended Print. Importing a profile never makes it tested or recommended.
Unsupported dialects retain the original file and explain that no supported
settings were found. Analysis does not convert, repair or rewrite the original.

The reader follows the named metadata fields in the
[OrcaSlicer 3MF implementation](https://github.com/OrcaSlicer/OrcaSlicer/blob/main/src/libslic3r/Format/bbs_3mf.cpp).
A matching field does not establish that the project is compatible with a printer
or safe to print. Use the slicer to inspect the complete project before printing.

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


## Archive review

Before importing an archive, review its structure and destination collection.
Choose separate Models or a Multipart Model with editable piece names. Each
piece creates or reuses a Model; the Multipart references those Models. It is
created only after every required piece and selected guide has imported.

Select PDF, Markdown, text files or images to preserve as Documents. In Multipart
mode they become its guides; in separate mode they remain Documents in the
archive's collection. Unselected and unsupported files are shown as omitted.
Retries reuse committed pieces and guides; they do not restore deleted guides
or reassign a guide that you attached elsewhere. Tasks links to saved results.

Folder selection in Bulk retains auxiliary files and defaults to the same review.
The browser prepares a lossless archive, up to 256 MiB and 2,000 files. For larger
folders, prepare an archive locally or choose direct separate Models, which shows
that auxiliaries will be omitted. Folder review uses managed storage. Direct
uploads retain their existing mounted-source destination controls. Neither flow
moves or rewrites the original files. Archive path, size and ownership checks
also apply to auxiliary entries. Multipart review can select a JPEG, PNG or WebP
cover, normalized through the existing image checks. If its publication fails,
Tasks retains the completed Multipart and offers retry; a later user edit prevents
a stale retry from overwriting that decision.
