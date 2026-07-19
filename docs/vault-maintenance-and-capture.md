# Vault Maintenance And Quick Capture

Settings → Maintenance provides two read-only audit modes. Quick checks owned
blob reachability/size, external links, thumbnails, Metadata, Revision
invariants, background work, and unclaimed storage. Full also streams SHA-256
for owned primary blobs, checks embedded-image references, and verifies the
available backup archives. Findings expose safe labels and identifiers rather
than server paths or signed URLs. Repairs are explicit, idempotent, and written
to AuditLog.

Pending Imports (`/inbox`) stores a capture before any Model or Artifact exists.
Resolution prepares safe review choices. Final import rechecks Collection access
and uses normal ingestion, whose `persist_artifact` service remains sole owner of
Model/Artifact/Metadata/thumbnail persistence. Failed captures remain retryable;
dismissal removes managed staging but never an imported Model.

Browser helper lives in `browser-extension/`. Load it unpacked, create a named
API key in Settings, and configure Vault URL. It sends current page URL/title
only to `/api/v1/inbox`; source resolution remains on server behind SSRF guards.

The model browser supports URL-restorable filters for Artifact type, material,
slicer, printer model, Revision status, printed state, print outcome, vault or
external storage, and upload date. Values within one group use OR; different
groups use AND; repeated tags retain AND behavior. Saved Views store the same
contract, so old views continue to load and new views restore all active filters.
