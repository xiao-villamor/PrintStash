# Capture, provenance, and browser extension

Use this reference for Pending Imports, URL/browser capture, provider
connections, Model Source provenance, capture staging, and `browser-extension/`.
Read [backend.md](backend.md) or [frontend.md](frontend.md) as well when the task
changes those layers. The user-facing behavior and privacy contract live in
`docs/vault-maintenance-and-capture.md`; limitations live in
`docs/known-limitations.md`.

## Contract owners

- `backend/packages/printstash-core/src/printstash_core/imports/contracts.py`
  owns portable capture contracts shared with the browser extension. Re-export
  public contract types through the package's `imports/__init__.py`.
- `backend/app/services/inbox.py` owns Pending Import state transitions,
  selection/retry behavior, and the handoff from durable review data to an
  import job.
- `backend/app/services/staging_leases.py` owns staging capacity, leases,
  expiry, reconciliation, and ownership transfer. A database row alone does not
  prove that staged bytes exist.
- `backend/app/services/import_resolvers.py` and `provider_connections.py` own
  server-side resolution and user-scoped source-provider credentials.
- `backend/app/services/provenance.py` and `source_covers.py` own Model Source
  snapshots, overrides, and cover lifecycle. API response composition still
  belongs in `services/model_views`.
- `browser-extension/capture-adapter.ts` and `capture-transport.ts` own the
  browser-to-server boundary; provider-specific page extraction stays in an
  adapter rather than leaking into transport or popup code.

Change the canonical owner and its consumers together. Keep old portable
archives and supported extension/server version combinations readable unless
the task explicitly defines a migration or compatibility cutoff.

## Security and lifecycle invariants

- Treat source pages, URLs, filenames, manifests, OAuth/provider responses, and
  extension messages as untrusted input. Use the existing URL/SSRF validation
  and bounded schema parsing paths.
- Retain normalized provenance, never raw HTML, source-site cookies, OAuth
  codes, signed download URLs, resolved credentials, or local staging paths.
- Provider credentials are per-user, encrypted at rest, redacted from audit
  data, and absent from API responses. Browser devices are named, scoped, and
  revocable.
- Browser-transferred bytes remain managed staging until ownership is
  atomically transferred to an import job or the item is dismissed. Retry,
  expiry, cancellation, and partial completion must not orphan bytes or claim a
  successful import.
- Capture metrics and errors use bounded labels/codes; never put URLs,
  filenames, provider payloads, or secrets in metric labels or logs.

## Browser-extension checks

The extension is WXT + TypeScript and uses the same oxc lint/format policy as
the frontend. Keep permissions and host access at the minimum required by the
implemented providers.

```bash
cd browser-extension
pnpm format:check
pnpm lint
pnpm typecheck
pnpm test
pnpm build
```

Run `pnpm test:e2e` when the change affects pairing, popup interaction,
provider extraction, upload/finalization, or browser-specific behavior. Build
the affected browser target(s), not only Chrome, when code or manifest behavior
is browser-dependent.

For a headline capture feature, the e2e evidence must cross the real boundary
the feature claims: extension adapter/transport to a contract fake or real
backend, and real-backend Playwright when the user-visible Pending Import or
Model Source flow changes.
