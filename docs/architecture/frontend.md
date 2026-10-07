# Frontend target architecture

Status: implemented ownership contracts through M10, with M11 integration qualification in progress (2026-10-07). The [plan](../frontend-architecture/plan.md) records decisions and dependency order; the [validation matrix](../frontend-architecture/validation.md) and individual milestone records distinguish accepted local results from outstanding final gates. This document describes source behavior, not a released version.

## Ownership and module interfaces

Keep React Router, Vite, React and TanStack Query. Use feature modules where a
workflow needs one owner for reads, writes, events and recovery; a directory move
without a changed contract is not an increment.

| Concern               | Owner / public interface                                                                                                                | Concrete problem resolved                                                                         |
| --------------------- | --------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Route composition     | Existing `router.tsx` + thin `pages/`; validate route parameters and compose public feature exports.                                    | Pages no longer coordinate sockets, transport caches and forms together.                          |
| Session lifecycle     | `lib/auth-store.ts`, `session-transport.ts` and Query bootstrap: identity, generation and cleanup.                                  | A delayed read, write response, asset or socket cannot act on a later session.                    |
| HTTP transport        | `lib/api/request`: typed error parsing, auth, cancellation, session checks, JSON/forms/actions/protected bytes.                         | Remove the second JSON freshness cache and transport knowledge of feature invalidation.           |
| Typed endpoints       | Existing domain endpoint files under `lib/api/`.                                                                                        | Keep actual HTTP/domain contracts explicit; no generic repository layer.                          |
| Feature remote state  | `features/library`, `features/printers`, `features/work` and named `lib/queries/*` owners: options, commands and affected reads.                          | Multiple surfaces observe one freshness/invalidation policy.                                      |
| Library navigation    | `features/library/url.ts`, `filters.ts`, `browse.ts`, `navigation.tsx`, `navigation-state.ts`, `reading-position.ts`.                                                                                   | One URL codec, one page order, one restoration policy for the nested scroll container.            |
| Event policy          | Event transport owns connections; feature adapters classify notices as read invalidation, controlled-refresh hint or authorized resync. | Events cannot independently install unvalidated private entity data or bypass stable-list policy. |
| Local operation state | Workflow controller where upload bytes, cancellation and retries have a lifecycle.                                                      | Client transfer progress does not get confused with the durable server Job.                       |
| Protected assets      | `lib/asset-cache.ts`, `use-authenticated-asset-url.ts`, `use-viewport-admission.ts`: acquire/release, viewport admission, byte budget and session disposal.                                      | Mounted images cannot lose their object URL to an unaware LRU.                                    |
| Shared UI             | Existing `@printstash/ui` primitives and app-level localized adapters.                                                                  | Preserve focus, overlays, tokens and motion without importing feature workflows into a package.   |
| Portable helpers      | Existing `@printstash/domain` pure formatting/domain helpers; app owns browser subscriptions.                                           | A package's environment assumptions remain explicit; no package split merely to rename it.        |

An options factory is justified when a read is observed, prefetched or updated
from multiple surfaces. Export the options/key contract, not a universal hook
that only forwards `useQuery`. Extract mutation coordination when list/detail
consumers need the same confirmed result. Keep a one-off endpoint or presentational
component simple.

### Implemented printer settings contract (M7)

`features/printers/queries.ts` owns the HTTP resources; live telemetry remains a
separate projection. `settings-edit.ts` owns the captured settings base, conditional
command, explicit authorized review and receipt publication for both detail and
card editors. Local fields and credentials stay in the component. A background
read cannot rebase the draft. Conflict or an uncertain response blocks another
write until review; revised save sends deliberate changes, and adoption explicitly
replaces the draft. A provider/auth-mode change requires adoption before credentials
can be submitted. Native form validation also applies to revised save.

`modules/printing/printer_edits.py` atomically claims the settings version and
rechecks current authority in the same transaction. The database contract tracks
settings writes, including legacy writers, independently of telemetry. A receipt
is captured while the write lock is held, before commit and hub reconnection.
The client never replaces an already-observed newer receipt or a different
history. Secret payloads do not enter Query/MutationCache. A denied or missing
printer read retires controls and the socket; explicit retry can restore an
authorized view. Session changes retire drafts, requests and private caches.

### Notification settings

`lib/queries/settings-notifications.ts` owns masked channel settings and the bounded
recent-delivery query. Printer choices reuse `printersOptions` and its existing
cache key. `NotificationsPanel` owns only its draft and pending gesture; failed
reads remain errors with independent retries, and do not fabricate Off or empty
success. Transient settings errors retain the draft read-only until recovery.

Commands keep credential-bearing arguments out of MutationCache, retire on session
change or disposal, cancel obsolete settings reads, and publish confirmed masked
receipts. Delivery history remains independent of a channel save. The form blocks
changes while a command is pending. Channel and master-switch writes require
independent captured editing bases. Background reads never rebase a draft;
revised saves send only deliberate field changes after explicit authorized review.
Conflicts or uncertain acknowledgements block ordinary retries. Replacement
channel/database histories require adoption and retire old credential input.
Adoption reads through the canonical query owner, keeping expired review previews
from replacing newer observations. Receipt publication preserves newer observed
versions, replacement histories and confirmed absence. Legacy external API writes
remain compatible and advance versions but do not themselves detect conflicts.
Evidence and remaining work: `docs/frontend-m9-notifications-validation.md`.

### Provider accounts and paired browsers

`lib/queries/settings-providers.ts` owns provider/device reads and their command
lifetime. These reads use native cancellation; transport has no JSON freshness cache. Non-secret connection/device receipts update or invalidate Query; Cults
credentials and temporary pairing codes stay outside Query and mutation history.
The panel owns drafts, confirmations and the active OAuth navigation handoff.
Session changes or disposal retire pending receipts. Transient read errors retain
mounted drafts with disabled actions; authorization failures hide private rows.

The backend now offers an additive conditional browser-name contract through
`modules/ingestion/browser_edits.py`. Its version excludes last-use telemetry;
its opaque history distinguishes replacement credentials and restored databases.
Legacy renames remain compatible. The first-party editor captures the original base with its draft, requires explicit
review after conflicts or uncertain writes, and allows revised saves only within
the reviewed pairing history. A replacement pairing requires adopting current
values. Denied or missing devices retire private drafts. Evidence: `docs/frontend-m9-provider-validation.md`.

### Implemented administration source seam (M9 increment)

`lib/queries/settings-library-sources.ts` owns the source catalog and exact
create/update/enroll/remove/scan gestures; `settings-config.ts` and
`settings-storage.ts` own their shared configuration/connection projections.
Settings, Similarity, first-folder setup and Upload consume those owners.
First-folder setup retains only the accepted source identity for scan retry.
Upload retains a local selected destination, never a second source catalog;
loss of eligibility blocks submission until recovery or explicit selection.
Accepted scans belong to TaskCenter even after their originating form closes.
Each administration workflow retains its domain-specific command and recovery contract.

### Conditional configuration forms (M9 incremental cutover)

`settings-config.ts` carries the form's captured editing base through cancellation
and validates the receipt before publishing it. A late acknowledgement cannot
replace a newer observed configuration. Secret-bearing payloads stay outside
MutationCache. OIDC captures its base at the first local edit; background reads
cannot silently change that base. Conflicts and uncertain receipts preserve the
draft and require an explicit fresh review before revised save or adoption.
Review state retires with the session; only sanitized server values are displayed.
The library-source feature toggle also sends its observed base and retains its
intended enabled/disabled value through conflicts or uncertain receipts. An
authorized review permits explicit revised save or adoption without another write.
First-folder connection also uses conditional activation. Conflict or uncertain
activation stops source creation until explicit review; an already-enabled fresh
read permits continuation without repeating the activation. Local folder drafts
and pending reviews retire with the session.
StorageConfigCard now replaces its JSON fingerprint with a captured editing base
and initial provider snapshot. Refetch cannot mix new untouched fields into an
existing draft. Explicit revised save merges deliberate overrides onto reviewed
current values; newer credentials typed during save retain their local intent.
`settings-preferences.ts` now owns immediate auto-mark, currency and thumbnail
width intents, plus backup/trash retention. Retention keeps a text/base pair from
the first edit; refreshing Trash does not read or overwrite configuration. Its
pending intent survives conflict; authorized review permits explicit revised save
or adoption. The revised retention value is validated before submission. Members
cannot edit retention, and an unavailable configuration read has explicit retry.
`settings-backup-policy.ts` owns the compound schedule/local-destination draft:
a captured base, a frozen snapshot and deliberate changes. Review shows authorized
current values; explicit revised save merges only deliberate changes onto that
snapshot. Config must acknowledge before remote-destination commands begin. Each
separate destination acknowledgement remains canonical if a later one fails; only
changed destination fields are sent, and confirmed drafts are removed individually.

The configuration transport and command owner now require an editing base for every
first-party write. The backend retains its documented legacy-client compatibility;
legacy writes advance the version but remain unprotected themselves. This cutover
covers vault configuration, not every editable aggregate. Remote connections use their own conditional version and authorized review contract; see `frontend-m9-storage-edit-validation.md`. Local-root enrollment now carries its exact
reviewed path through the separate enrollment command.

### Implemented backup seam (M9 increment)

`settings-backup-catalog.ts` owns independent exact-source and optional discovery
reads; `settings-backup-commands.ts` validates reviewed source identity, coordinates
cancellation and publishes confirmed backup receipts. `settings-backup-runs.ts`
owns execution history and destination retry. The component retains only drafts,
confirmation snapshots and local feedback. Configuration and connection owners
publish each policy transaction; a later failure does not erase earlier ACKs.
This does not imply an atomic backend policy transaction. Process catalogs read
again on return, so accepted work completed away from the view is recoverable.

### Implemented migration process owner (M9 increment)

`settings-vault-migration.ts` owns history, per-run status, reports and explicit
migration commands. Active runs use foreground Query polling; a failed read stops
that polling until explicit recovery. The minute clock in the panel only updates
expiry/grace labels. The panel retains selection, destination/policy drafts and
reviewed confirmation snapshots. Provider and backup catalogs use their existing
shared owners. Commands cancel obsolete reads before dispatch and publication;
uncertain replies trigger a status read, never an automatic replay. Credentials
stay in the local form and active call. Backend digest/state/identity/audit/backup
checks remain authoritative; these DTOs do not expose a comparable edit revision.

### Current storage configuration (M9 frontend ownership increment)

`StorageConfigCard` observes the existing configuration/provider owners and keeps
only field overrides, reviewed root identity and local feedback. Configuration ACKs
publish centrally; edits typed while a save is pending remain drafts. Migration
activation/recovery invalidates the configuration projection after canceling older
reads, so current-location summaries follow the accepted storage transition.
`settings-storage-root.ts` scopes explicit enrollment to its view/session and checks
the currently observed reviewed path. The first-party enrollment transport requires
that path and sends it as `expected_path`. The API captures the active local root
once, rejects a different reviewed path with 409 `storage_review_changed` before
identity or marker writes, then enrolls the captured root. Path comparison is exact;
it does not resolve symlinks. A changed-root response closes confirmation and asks
for fresh review. Legacy requests omitting the path retain role-only compatibility
and do not receive this protection. Filesystem identity after a mount/symlink
replacement remains subject to the existing enrollment and marker checks.

The configuration PUT now stages its owned policy/flag/schedule/provider writes and commits them together before publishing
runtime values or derivative hints. A database failure rolls back the whole patch.
`SystemConfig.vault_edit_version` now advances transactionally for every editable
vault configuration field, including legacy SQL/ORM writes and provider credentials.
The same immutable database trigger installs through migrations and fresh bootstrap;
backup-attempt timestamps and independently owned feature settings do not advance it.
`administration/config_edits.claim` supplies the database compare-and-advance
operation and rechecks current administrator/session authority; callers own rollback
of the whole patch. Configuration GET now returns a coherent persisted editing
snapshot and matching ETag. PUT accepts `If-Match`, requires it for
`X-PrintStash-Edit-Contract: conditional-v1`, and rejects stale bases with 412.
Legacy writes remain accepted and advance the version. A receipt is captured
before releasing the write transaction; later commits cannot replace it. Runtime
publication reads the latest locked row and only publishes the edited fields.
Settings conflict recovery and independent aggregate contracts have separate qualification records. The root-enrollment precondition is independent
of vault configuration edit versions.

## Implemented directory structure

The original proposal grouped every workflow under `features/`. The implementation keeps existing query modules where they already provide a clear behavioral owner. A cosmetic move is unnecessary; the import gate and explicit interfaces enforce responsibility.

```text
frontend/
  src/
    main.tsx, router.tsx        # bootstrap, access gates and React Router
    pages/                     # route composition and route-local drafts
    features/
      library/                 # browse, edits, taxonomy, history and builds
      printers/                # catalog and conditional settings commands
      work/                    # durable work queries
      auth/, setup/            # entry contracts
    lib/
      api/                     # HTTP transport and typed endpoint clients
      queries/                 # named workflow read/command owners
        documents.ts, captions.ts, inbox.ts
        profiles.ts, search.ts, similarity.ts, statistics.ts, share.ts
        settings-*.ts          # independent administration workflows
      auth-store.ts            # validated identity and lifecycle notification
      session-transport.ts     # generation fencing and cancellation
      query-client.ts          # bootstrap/defaults and cache cleanup
      events.ts                # connection lifetime, no entity cache
      asset-cache.ts           # protected bytes and leased object URLs
      task-center.ts           # durable Job progress and terminal waiters
      artifact-upload.ts       # local byte transfer/resume protocol
      model-upload-workflow.ts # accepted upload orchestration
    components/                # presentation, drafts and feature composition
      ui/                      # localized app adapters
    types/                     # explicit shared wire/domain contracts
  packages/ui/                 # reusable presentation primitives
  packages/domain/             # shared pure helpers and explicit preferences
  tests/e2e*/                  # browser behavior and delivery contracts
browser-extension/             # independent capture client
```

Create a module only when its behaviour is migrated. Keep reusable domain enums
and DTOs at the lowest owner that has real consumers; do not duplicate them in
every feature or move all types into a new universal package.

```mermaid
flowchart TD
  App[Bootstrap and React Router] --> Feature[Feature public interfaces]
  App --> Session[Session lifecycle]
  Feature --> Queries[Query options and mutation policy]
  Feature --> Local[URL, drafts and preferences]
  Feature --> UI[UI primitives and localized adapters]
  Queries --> API[Typed endpoint clients]
  API --> HTTP[HTTP and session fencing]
  HTTP --> Backend[FastAPI capability contracts]
  Events[Event connection] --> Policy[Feature event policy]
  Policy --> Queries
  Feature --> Assets[Protected asset lifecycle]
  Assets --> HTTP
  UI --> Helpers[Portable helpers]
```

Allowed imports flow toward lower-level contracts. Endpoint clients and transport
cannot import React, feature queries, components or routes. Workspace packages
cannot import app source. Feature internals cannot import route composition or
sibling internals: shared screens compose public interfaces, and shared DTOs live
at their actual common owner. Session and event infrastructure exposes lifecycle
signals without importing feature code; bootstrap wires cleanup and adapters.

Migrated boundaries are enforced by `frontend/tests/repo/dependency-boundaries.test.ts`
using the existing Oxc parser and a resolver for TypeScript aliases and workspace
exports. The [boundary contract](../frontend-architecture/dependency-boundaries.md)
names the public feature modules. Transport has no Query exception. The gate checks type-only, static, re-export, literal dynamic and worker
imports, reports runtime and type-involving strongly connected components separately,
and fails on unresolved computed imports for explicit review. Unused exceptions
fail too. This is dependency-direction enforcement, not a claim that every legacy
owner has migrated; no ESLint or new build framework is required.

## State ownership

| State                                                               | Source of truth                                                  | Lifetime / rule                                                                                                       |
| ------------------------------------------------------------------- | ---------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Server entities, lists, counts, capabilities, remote process status | Feature-owned Query entries backed by authorized endpoints       | One cache, complete keys, declared invalidation. No transport JSON TTL.                                               |
| Collection, filters, sort, library mode, selected route entity      | URL                                                              | Parse/normalize once. URL wins over preference; preference supplies an absent initial default only.                   |
| Form draft and base editing identity                                    | Editing feature                                                  | Preserve dirty values across refetch/errors; explicit conflict resolution. Never persist secrets as a convenience.    |
| Selection, open modal, hover, active local tab                      | Nearest owning component/workflow                                | Clear or reconcile when its view identity changes.                                                                    |
| Locale, theme, grid preferences                                     | Preference owner                                                 | Shared subscription; validated/versioned persisted values. Private preferences are scoped and cleaned appropriately.  |
| Displayed library ordering                                          | Query data for that view/session + bounded presentation metadata | No second indefinite entity cache. Freeze ordinary external reordering until refresh; patch confirmed own actions.    |
| History position                                                    | Entry-keyed restoration metadata                                 | View identity, entity anchor, relative offset and fallback pixel position; no private entity payload in localStorage. |
| Upload bytes, local retries and local cancellation                  | Transfer controller                                              | Separate from the server Job; dispose explicitly.                                                                     |
| Authenticated image/preview blobs                                   | Asset lifecycle                                                  | Session-bound bytes and leased object URLs; bounded idle memory.                                                      |

Library view identity includes normalized URL inputs and validated session context.
History entry identity is distinct: two visits to the same URL can have different
positions. Keep a session epoch as well as user identity; logout/login to the same
account still invalidates old async work. Clearing Query alone does not stop a
late mutation callback or body reader.

## Queries, mutations and events

Keys include every response-shaping input: scope, filters, sort, page size and
permission/session partition where appropriate. Normalize set-valued filters.
Cursor is `pageParam`. Forward Query's AbortSignal to the actual fetch/body work;
classification of abort must not log a user-visible failure. Session generation
must be checked before any result publication or unauthorized-response handling.

Maintain the existing general freshness defaults unless the feature has a reason
to differ. Library browse is such a reason: automatic focus/reconnect refetch must
not silently reorder the displayed infinite list. Revalidate its revision and
show available changes instead. Queries outside that stable-list contract can
continue ordinary background revalidation. Permission/auth failures bypass it.

Guard continuation against any in-flight list fetch, not only `isFetchingNextPage`.
Use `cancelRefetch: false` as appropriate to avoid replacing an active refresh;
it is not a parallel-fetch strategy. Retain existing pages after continuation
failure, and provide Retry separately from an empty-success state. On a stale
browse revision, offer Refresh; do not retry an invalid cursor automatically.

A mutation establishes a boundary for older reads before writing. After server
confirmation, publish its authoritative result to relevant existing Query entries
before showing success. Cancel/reconcile reads that started earlier, including
reads started while the write was pending. Version-aware reconciliation prevents
an older body from replacing a confirmed edit. Serialize conflicting operations
per entity or reject duplicate submissions; do not serialize unrelated entities.

Keep ordinary external changes, confirmed own changes and permission changes
explicitly distinct. In Favorites, a confirmed unstar removes the card without
rebuilding unrelated pages; if it invalidates the browse revision, continuation
requires refresh. A full-list rollback must not undo another successful mutation.
Where optimistic UX is justified, roll back only that operation's owned fields.
The selected favourite-removal policy itself is confirmation-first.

Keep invalidation in feature mutation/event policy. The M10 cutover removes the
HTTP-path invalidation bridge. Taxonomy commands own labels/choices/counts; printer
catalog commands own catalog/dashboard refresh. Library metadata publication
revalidates derived reads while preserving the acknowledged detail and controlled
browse snapshots. Endpoint clients serialize requests and decode receipts only.

Source editing is owned by `features/library/provenance.ts`. The provenance DTO
contains its Model editing version and nullable cover metadata; components must
not pair it with a later version fetched independently to authorize a write.
Freeze the field/cover command at the user's gesture. Conflict or unknown outcome
retains that command and requires fresh Model/Source reads with matching versions
before explicit retry. Permissions come from that reviewed Model. Source
acknowledgements retire earlier reads; cover image cache identity follows the
returned cover metadata, while all bytes still use authenticated asset leases.
The Source endpoint clients require a version; no transport cache or generic
form/repository layer is added. See [the Source validation record](../library-provenance-editing-validation.md).


Lightweight outliner Model/Multipart entries carry their stored editing version
alongside name and location, including search and continuation pages. Collection
search matches are a distinct unversioned case. A move must carry the version
captured from its source gesture; fetching a current version just to authorize a
stale move defeats conflict detection. The read contract is qualified in
[the movement validation record](../library-move-validation.md); the gesture and conditional movement owner preserve that base through review.

Multipart detail publication stays in `features/library/multipart.ts`. Composition,
tag and cover writes use conditional versions; their endpoint clients reject a
receipt for another aggregate or a non-advancing version. The shared tag editor
accepts an optional review capability, whose commands the Multipart caller binds
to a fresh authorized snapshot. Cover recovery retains its File/delete intent in
the existing detail review flow. Auxiliary confirmation updates only its owned
fields in an open composition draft; it advances that draft's base only when the
command used that same base. Reviewing an auxiliary edit never silently rebases
unrelated input. Cancelled editor signals also fence receipt publication.
See [Multipart qualification](../library-multipart-validation.md).

Events are hints to obtain authorized state. Reconnect resync must recover missed
notices; socket generation/disposal prevents late-ticket connections from reviving
a dead subscription. Browser focus and offline recovery are part of the same
feature lifecycle. Preserve command-specific polling where completion controls the
next action; consolidate duplicate remote readers rather than banning all timers.

## Route, loading and recovery contracts

Use the existing route structure and URLs. Preserve same-origin cookies, public
share isolation, setup/auth gates, history and return destinations. Reject unsafe
external return targets. Replace invalid/retired query values canonically without
adding a history entry. Route navigation remains unanimated under DESIGN.md.

A library transition renders one coherent source or destination snapshot: heading,
folder breadcrumbs, outliner context and cards agree. While showing a prior
snapshot, actions cannot accidentally target the destination URL. An error keeps
a recoverable state with clear scope; it cannot masquerade as an empty collection.

Distinguish initial loading, empty success, initial failure, background failure,
continuation failure and conflict. A stale result may stay visible only when its
session/permissions permit it. A recoverable chunk error offers reload with draft
loss considered; repeated automatic reloads are not recovery. PWA handles static
shell/assets only according to the existing contract; never cache private JSON
or authenticated bytes across identities by accident.

Restore the actual `main` scroll container only after matching list data/layout
are ready. Prefer entity anchor plus offset, then bounded fallback. Cache eviction,
changed viewport, a deleted anchor and session loss each need an explicit outcome.
Do not add private Query persistence simply to preserve navigation within a tab.

The implemented Library navigation interface is `LibraryItemLink` / `LibraryBackLink`.
Links carry a validated same-origin Library return URL plus the originating Router
entry identity. Ordinary Back reuses the immediate registered entry; modified
clicks remain native, and unknown origins use the URL fallback. `navigation-state`
keeps at most 64 entry records containing only session identity, URL, container
positions, anchors and loaded-page counts. It clears on session/access retirement;
it never stores entity responses. `reading-position` restores grid/list containers
after the matching snapshot settles, requests no more than the recorded page
counts after Query GC, and displays a reset notice if the anchor cannot be restored.
Two visits to the same URL retain separate bookmarks. The displayed snapshot,
including breadcrumbs and command targets, owns its origin while another route loads.

`lib/navigation.ts` exposes only push, replace, Back and Forward. Do not add route
refresh, route prefetch or scroll options without an actual implementation and an
observable contract. Query options own real data prefetch; feature commands own
invalidation; Library owns reading-position recovery.


## Replaced mechanisms and current owners

| Original responsibility                                     | Current owner / removed mechanism                                                                               |
| ----------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `components/model-grid.tsx` merge, sort, membership         | library browse + authorized server page; delete local mixed ordering and group downloads.                        |
| model-grid URL state / history listeners                    | library URL codec + React Router location; remove synchronized mirrors.                                          |
| model-grid settled view / detail return                     | library presentation/history; preserve coherent snapshots without a permanent duplicate cache.                   |
| `components/model-card.tsx` StarOverride                    | library mutation policy; all surfaces receive confirmed state.                                                   |
| `lib/queries.ts` and central path invalidation              | owning feature options/mutations; keep shared catalogs shared.                                                   |
| saved-view effect / `document-browser.tsx` loaded documents | feature Query entries; draft/local selection remain local.                                                       |
| `bottom-nav-bar.tsx` and Inbox refresh                      | shared Inbox key/lifecycle.                                                                                      |
| `lib/task-center.ts`                                        | tasks remote reads + preserved local transfer protocol.                                                          |
| printer-detail WebSocket effect                             | printers live connection with owned cancellation/disposal.                                                       |
| `settings-panel.tsx`, storage/provider panels               | settings workflow owners, draft forms and remote Job queries.                                                    |
| asset cache / thumbnail hooks                               | leased, bounded, session-aware asset owner.                                                                      |
| `lib/navigation.ts` and link no-ops                         | explicit React Router navigation and real feature prefetch/restoration; delete misleading compatibility options. |
| package browser preferences and app wrappers                | explicit app preference subscriptions; retain proven package helpers and UI primitives.                          |
| extension capture/auth clients                              | independent extension boundary, tested against FastAPI; do not import the web app's Query cache.                 |

## Implemented Model movement owner (M4)

`features/library/moves.ts` owns conditional Model movement and its recovery.
Native cards/list rows and paginated outliner leaves capture a Model's displayed
version and session at drag start. Drop targets supply the intended collection;
they never fetch a newer version to authorize an older intent. `lib/model-dnd.ts`
validates native DataTransfer input and rejects id-only or retired-session drags.

The owner permits one outstanding intent per Model and concurrent independent
Models. A412 or uncertain response retains the original destination. Only an
explicit authorized read enables a reviewed retry or adoption of the current
location. `components/model-move-review.tsx` presents this state using shared
primitives; it owns no server cache. Confirmation cancels an obsolete detail read
before version-ordered publication and coordinated Library refresh. Navigation,
unmount and session retirement fence publication and abort outstanding reviews.
Every first-party `updateModel` call now requires its editing base.

## Contributor conventions and tests

Tests live beside the behavioural owner under `__tests__`; packages test their
public interfaces. Frontend component tests use the real router, Query and
transport with controlled fetch responses. Browser tests own real scroll, navigation,
viewer/worker and PWA delivery behaviour. Backend integration tests own filtering,
permissions, versions and persistence using real SQLite and relevant Postgres
cases. Architecture tests own dependency directions, not filenames or file sizes.

Before adding an abstraction, identify the duplicated lifecycle or invariant it
owns, its public inputs/outputs and the old mechanism removed. A forwarding wrapper,
file split or a universal repository is not a sufficient reason. Production work
ships with the repository's behaviour matrix and applicable tests in the same PR.
Update this document's migration status as owners actually move; do not document
a proposed guarantee as available behaviour.

### Editing identity across restoration

An editing base is `{edit_epoch, edit_version}`, captured with the authorized
Model, Multipart Model, Document or Source snapshot when the user starts an
intent. `lib/api/editing.ts` validates and serializes that public protocol;
`types/editing.ts` defines its value type. These helpers do not own data or fetch a
replacement base. The server compares the pair atomically. Batch acknowledgement
and undo also carry pairs. Keep invalid or absent bases out of existing-entity
editors; a new unsaved Document has a distinct shape with no server editing base.

Counters order edits only within one epoch. `features/library/editing.ts` compares
snapshots using the epoch the operation observed before starting. Model,
Multipart, Source, movement and Document publication use that rule: an old receipt
cannot replace a different history installed while its request was in flight.
Explicit review can adopt a restored history whose counter is lower. Capture the
publication authority before that review request, including when adoption is a
later user action. Do not infer epoch ordering or look up a new epoch to authorize
an old draft. Session retirement remains a separate, stronger fence.

The backend projects the singleton incarnation in the aggregate SELECT; it is not
a new persisted entity column, per-row request, cache or migration. Contributor
changes to an editing DTO must update conditional clients, acknowledgement
validation, factories and OpenAPI in one increment. Qualification is recorded in
[the restoration matrix](../library-restore-editing-validation.md).

Own confirmed Favorites removal uses the existing navigation-position metadata.
Card actions identify their associated item, including buttons beside its link.
The reading-position owner observes the acknowledged neighbor promotion and
applies it to the mounted view only after the old card leaves the displayed
snapshot. It does not scroll early when confirmation precedes rendering, and
a queued event with unchanged offsets cannot discard the promotion. Native
scroll bounds still apply. See [the mutation matrix](../library-mutations-validation.md).

### Interrupted Library batches

`features/library/batch-edits` owns selected editing bases, bounded requests and
conditional undo. Its result separates acknowledged successes/rejections from an
interrupted request's unknown identities and the remaining unattempted identities.
A malformed receipt confirms none of that request; earlier valid receipts survive.
Consumers must not turn unknown outcomes into successful or failed rows, discard
prior confirmations, or authorize retry with a background read. Model batch undo
uses only the exact acknowledged editing identities.

`LibraryBatchRecovery` presents a transient command receipt and original intent,
with links for explicit current-state review. Undo results remain separate so
undoing confirmed changes never erases the original uncertainty. Session and view
retirement suppress delayed feedback. This state is not an entity cache and does
not participate in Query freshness. Collection commands remain a distinct contract.


## Implemented protected-image ownership

`acquireAssetUrl` returns a shared-download lease with an idempotent release.
`useAuthenticatedAssetUrl` acquires that lease even for a synchronous cache hit;
`useViewportAssetUrl` admits a persistent frame once it is within200px of the
viewport. Keep the lease while an image is mounted, including after scrolling
it away. Queue only four protected downloads; release cancels unneeded queued or
active work without aborting another consumer's lease. Auth/access retirement
revokes private URLs and retires requests before replacement-session work starts.
Explicit path invalidation makes mounted consumers reacquire fresh bytes.

Only inactive entries are evicted, under both400-entry and32MiB encoded-Blob
budgets. Live lease bytes are reported separately and decoded image/GPU memory
is not inferred from either counter. `ProtectedThumbnail` marks readiness after
actual decode of its current connected image. Missing derivatives and failed
images cannot masquerade as successfully decoded thumbnails. Native external
covers retain their existing transport semantics. The [M6 comparison](../frontend-m6-performance.md)
records the measured settings, extra root module requests and remaining limits.

Paginated outliner branches publish their own completed Query pages. Do not
reintroduce an all-branches promise barrier for remembered expansion. A slow
branch must not prevent another settled branch from being read or selected.

Capture selection anchors and acknowledged-intent metadata once in the gesture
handler. Functional React state updaters must remain pure: React can replay them,
and moving an anchor inside the updater changes the meaning of a Shift range.
The existing ModelBrowser controller owns selection; this does not require a
second selection store or a generic state abstraction.

### Accepted uploads (M7 checkpoint)

`lib/model-upload-workflow.ts` owns accepted single, linked mesh/G-code and
sequential bulk execution under one captured session. `UploadModal` owns form
drafts and hands off accepted work; closing it is not cancellation.
`artifact-upload.ts` owns byte transfer and resume; `task-center.ts` owns durable
Job progress and terminal waiters, with event wakeups and a completion-chained
fallback poll. A session change stops remaining bulk dispatch and publication.
Do not add component-local Job timers or copy Task Center progress into a second
remote store. The ZIP review reads one immutable completed manifest through Query, distinct
from live Job progress. Key the draft by Job and retire it with its session;
cancel an abandoned read and offer Retry on failure. Attachment reconciles early
server discovery into the named local workflow, preserving review metadata.
Never let a delayed receipt close a replacement review. M7 browser closure
qualification is recorded separately.

### Manufacturing reads and confirmation (M8)

`features/library/builds.ts` owns Build list/detail identities and acknowledged
publication. Build versions are the existing manufacturing contract; do not apply
Model edit epochs to them. Reads consume cancellation and poll only in the
foreground, stopping on error. A confirmed command cancels obsolete detail reads,
validates identity/version and preserves a newer already-known record. History
and Fleet invalidation belongs here, not in HTTP transport.

The Builds page owns filters, creation/duplicate forms and per-attempt result
drafts. A draft captures its attempt version and idempotency key; background data
cannot remount or overwrite it. Explicit latest review changes its base/key while
preserving entered units. Retrying an uncertain result keeps the original key.
Canonical Model/Multipart/printer queries supply secondary catalogs. No local
server-response arrays or independent polling interval should return. Definitive
read denial hides protected cached content; transient failure offers recovery.

Profiles and Statistics apply the same denial distinction. Profile conditional
editing is now qualified in the [preset matrix](../frontend-m8-profile-edit-validation.md).
`lib/queries/profiles.ts` owns captured-base commands, review reads and monotonic
receipt publication. The row component owns its draft and authorized review
snapshot; blur never resubmits a conflicted or uncertain edit. Revised save sends
only deliberate overrides. Backend `modules/printing/profile_edits.py` owns the
atomic claim and current authority check. Its epoch binds database history to the
preset incarnation, including reuse of a deleted SQLite ID. Spoolman-linked rows
remain read-only. Do not bypass these commands with unconditioned `sendJson` calls.

Search settings use an independent version for opt-ins and ranking settings;
vault configuration and indexing progress do not invalidate those drafts.
`modules/search/settings_edits.py` owns backend claims, and `configuration.py`
merges PATCH fields only after reserving the singleton. PUT/PATCH responses carry
the accepted transaction snapshot. Legacy writes invalidate newer editors but
remain explicitly unprotected themselves.

`lib/queries/search.ts` owns first-party conditional commands and authorized
review reads. The advanced form retains its original base across refetches;
revised replacement preserves settings the user did not change. Guided setup
stops before download/preparation after an uncertain or conflicting settings save,
requires explicit review/adoption, and never retries a gesture automatically.
Review snapshots remain local to the active editor. See the [Search matrix](../frontend-m8-search-edit-validation.md)
and the [earlier M8 qualification](../frontend-m8-validation.md).

### Static delivery recovery

The service worker owns navigation shell and explicit public static paths only;
API and Authorization-bearing requests never enter its cache. Shell version v6
retires the prior broader cache. Network bootstrap delivery does not await cache
writes and continues when Cache Storage rejects. Deferred imports get at most one
automatic reload per tab session; an unrelated successful import cannot reset
that budget. An eager RouteError uses the existing UI primitives and localized
reload/back actions when a route still cannot load. No server rendering/runtime
or routing-framework change is required. Evidence: `docs/frontend-m10-validation.md`.

### Spoolman settings

`lib/queries/settings-spoolman.ts` owns the masked read and conditional command lifecycle. Connection drafts capture an editing base; status/probe refresh cannot replace typed fields. A conflict or uncertain write requires explicit current-state review. Adoption discards old secret input, and a changed database history cannot authorize replay of the old draft. Secrets remain outside MutationCache. Server claims and the independent Spoolman counter commit with all edited fields; telemetry does not advance that counter. Existing unversioned external clients remain compatible and unprotected. See [the integration correction](../frontend-m11-spoolman-validation.md).

### Setup admission and transient route state

`SetupGate` owns the setup-status decision for each pathname entry. Before the
first accepted response it mounts no application content. During a later probe,
React Activity hides the admitted subtree and cleans up its effects while
preserving transient state, such as a newly created document's editor mode. A
redirect retires that subtree with a new identity; the private AuthProvider
session key independently removes it on session changes. Never implement a
loading indicator by discarding an already admitted editor's state.

The storage card accepts the existing legacy configuration contract: an empty
`storage_provider` means the explicit `storage_backend` and its native path/S3
fields identify the configured storage. It is not an unknown catalog provider.

The authenticated first-run guide uses `features/setup/guide.ts`: cancellable
first-five Model and directory-discovery queries, plus entry/session-scoped storage
preparation. `SetupStorageChoice` reuses the canonical provider/configuration queries
and owns only its draft. Changing language or refetching metadata cannot overwrite
a dirty draft; retired preparation cannot advance the guide. Secret-bearing setup
commands remain outside MutationCache.

Before any Library cursor is accepted, one `browse_refresh_required` response can
retry the initial page once. A second conflict exposes explicit refresh recovery.
Continuation conflicts never retry automatically or replace an accepted snapshot.
