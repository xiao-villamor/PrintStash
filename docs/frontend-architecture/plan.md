# Frontend architecture migration plan

2026-10-07. **Implementation in progress. M2 acceptance is locally qualified; no milestone is formally closed while its prerequisites remain open.**
The approved scope covers the entire first-party frontend incrementally, including
workspace packages, the browser extension and integration contracts. Execution was authorized after consolidating the plan and its accepted answers. A smaller library fix is not completion
of this plan.

Start with the [evidence and review gaps](review.md), [target architecture](../architecture/frontend.md),
[dependency inventory](dependencies.md), [per-file ledger](review-ledger.tsv) and
[behaviour matrix / validation protocol](validation.md). The ledger identifies
what was inventoried versus actually inspected; this is not a completed exhaustive
manual audit.

## Settled product decisions

| Decision             | Required behaviour                                                                                                                          |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Scope                | All frontend areas, in runnable increments; retain existing design and capability gates.                                                    |
| Library modes        | Everything and Multipart Sets only. Remove Organized and Parts only controls. Models remain independently addressable.                      |
| Legacy selection     | Map retired modes to Everything silently, preserving folder, filters and sort. Organized was also a stored preference, not a dedicated URL. |
| Router               | Keep React Router; improve the existing TanStack Query integration.                                                                         |
| External changes     | Keep the displayed list stable and announce available changes. Refresh explicitly or on navigation to a new view.                           |
| Continuation         | After a membership/order change, refresh before loading another page. A server check must also catch missed events.                         |
| Own favourite action | In Favorites, remove the card after server confirmation; preserve the reading anchor. Failure leaves the card present.                      |
| Concurrent editing   | Detect conflicts, retain the draft and offer review/reload. Never silently overwrite another edit or auto-resubmit a stale draft.           |

The controlled-refresh decision is recorded in [ADR 0017](../adr/0017-controlled-library-refresh.md).
Permission loss and logout override visual stability. Returning with browser Back
restores that history entry when its session and accessible data remain valid;
it is not equivalent to navigating to a new filter or collection.

## TanStack: three distinct choices

The inspected installation has React 19.2.7, React Router 7.17.0, Query 5.101.0,
Vite 8.2.2, TypeScript 7.0.2 and Node 24.19.0. Query's installed React peer range
is `^18 || ^19`; Router requires React >=18 and Node >=20. Keeping this stack is
compatible without adding a runtime. Package manifests declare ranges; see the
separate dependency inventory rather than treating every range as a resolved version.

| Option                     | Benefits                                                                                                                                            | Cost, limits and operations                                                                                                                                                                                                                                                 | Decision                                   |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------ |
| A. React Router + Query    | Reuse working routes, auth and deployment; correct query identity, cancellation, mutation reconciliation and URL ownership at their existing seams. | Requires explicit URL codecs and scroll-container restoration. Does not solve server pagination by itself. Lowest migration effort.                                                                                                                                         | Chosen.                                    |
| B. TanStack Router + Query | Typed routes/search parameters; route loading and intent prefetch can coordinate with Query.                                                        | Rewrite route tree, navigation shim, links, auth boundaries, error/loading and history tests. Avoid two remote caches by using Query as the data owner. Static hosting can remain. Medium effort without a demonstrated payoff for current failures.                        | Defer.                                     |
| C. TanStack Start          | Mntegrated server functions, SSR/streaming and prerendering when a product needs them.                                                              | Full-stack build and execution boundaries must be reviewed alongside FastAPI, cookie forwarding, CSRF, cache isolation, assets, PWA and reverse-proxy rules. Highest effort. Start also supports a static SPA mode; an extra server runtime is conditional, not inevitable. | No current requirement justifies adoption. |

Official Query guidance describes the shared fetch lifecycle of infinite pages
and the need to consume its cancellation signal. These address observed races
without changing routers: [infinite queries](https://tanstack.com/query/latest/docs/framework/react/guides/infinite-queries),
[cancellation](https://tanstack.com/query/latest/docs/framework/react/guides/query-cancellation).
TanStack Router can integrate an external cache such as Query:
[external data loading](https://tanstack.com/router/latest/docs/guide/external-data-loading).
Start's [overview](https://tanstack.com/start/latest/docs/framework/react/overview)
and [SPA mode](https://tanstack.com/start/latest/docs/framework/react/guide/spa-mode)
distinguish server capabilities from client-only deployment.

Registry metadata checked on this date: `@tanstack/react-router` 1.170.41 admits
React >=18/19 and Node >=20.19; `@tanstack/react-start` 1.168.60 admits React >=18/19,
Vite >=7 and Node >=22.12. Start also declares an Rsbuild peer; installation needs
its peer metadata and chosen adapter reviewed. The installed major versions meet
the cited React/Node/Vite ranges. Neither alternative was installed, compiled or
browser-qualified here; peer compatibility is not end-to-end compatibility.
Primary metadata: [Router package](https://registry.npmjs.org/@tanstack/react-router/latest),
[Start package](https://registry.npmjs.org/@tanstack/react-start/latest).

The authenticated library has no demonstrated indexing or SSR requirement.
SSR would not repair ordering, query races or thumbnail fanout and would add
request-scoped private-cache/hydration responsibilities if enabled. Public share
previews may warrant separate investigation; no measurement currently justifies
moving the whole application. Keep the Vite SPA, same-origin FastAPI API, cookie
authentication, client navigation, static assets and existing PWA delivery.

Do not add TanStack Store, DB, Form, Table, Virtual or query persistence as a bundle.
Use existing local state for drafts and selection. Consider Virtual only after
profiling proves mounted DOM/render cost remains material and keyboard navigation,
DnD and scroll anchors have acceptance tests. Evaluate Form only against a concrete
repeated validation workflow. Persisting private queries adds expiry, revocation and
account-isolation contracts; it is not needed to fix Back within a running tab.

## Contracts that must exist before UI simplification is declared correct

### Ordered browse pages

The server owns authorization, live/trashed scope, all list filters and ordering
before pagination. Return a discriminated entry (`model` or `multipart-model`),
stable entity identity, the next opaque cursor and the browse revision. Preserve
that order in the browser. Everything includes each eligible Model once plus each
eligible Multipart Model once; a reference does not hide its Model. Multipart Sets
returns eligible Multipart Models. Collection navigation remains an outliner concern.
Counts must state their unit: a combined card count cannot silently replace the
existing count of Models.

Build this in the existing library capability using its permission scopes and
read-model projections. Prefer extending an existing suitable browse owner over
creating parallel list services. Model-only API consumers keep their current
contract until deliberately migrated. Define every mixed-sort field: comparable
name/date values, a documented null position for kind-specific values, then a
stable kind/id tie-break. Do not sort each fetched prefix with `localeCompare`.
Specify server collation for SQLite/Postgres and exercise Unicode names and ties.

Remove the separate 500-group membership downloads when the retired modes go.
Their historical empty-page failure is not a reason to build a more complicated
client membership cache. All remaining filters still run before the page cut.
A page with zero displayed entries must never hide an advertised continuation.

### Revision-checked continuation, not historical snapshots

A keyset cursor identifies a position; it does not freeze mutable rows. Bind it to
normalized view/filter/sort/page-size inputs, caller authorization context and a
transactionally maintained browse revision. Reject incompatible or stale cursors
with a typed response that leads to Refresh, not silent concatenation or retries.
Check permissions on every read; a cursor is never an authorization token.

Start by assessing a conservative database-backed library revision. Every write
that can change list membership/order must advance it in the same transaction:
relevant metadata, tags, moves, trash/restore, membership where filters depend on
it, permission changes and background publication. An inventory of all writers is
an acceptance artifact. Capture rows and revision consistently within the read
transaction; if that cannot be guaranteed, detect the race and reject the page.
A timestamp watermark or a WebSocket notification alone is insufficient.

A global revision may invalidate unrelated lists and become a write-contention
point. Measure this at supported scale before acceptance; narrow the revision scope
only when the affected-view contract is proven. Do not hide an incomplete writer
inventory behind a claim of coherent pagination. An event only prompts validation;
focus/reconnect and continuation check authority again. No event-delivery guarantee
or durable server snapshot is introduced.

Refresh rebuilds the list from page one. Retain the current anchor if its entity
can be found within bounded reconstruction; otherwise show a clear reset to the
start. Rapid writes may repeatedly require refresh: record their frequency and
latency before declaring the design usable. Partial new pages cannot replace part
of an old displayed list.

### Conditional edits

Add an explicit edit version per user-editable aggregate, beginning with Model,
Multipart Model and Document. A version is not an Artifact version, a G-code
Revision or the browse revision. A conditional write must compare and advance the
version atomically in the database. Two writers using one base version cannot
both succeed. A client-side comparison or timestamp precision is not sufficient.

Prefer a documented `If-Match`/ETag precondition: `412 edit_conflict` for a stale
version and, after compatibility cutover, `428 edit_precondition_required` when
the required precondition is absent. These are proposed API contracts, not current
responses.
Define missing-precondition handling and compatibility before enabling enforcement;
update OpenAPI, clients and factories together. During an additive rollout,
unversioned legacy writes must advance the version so newer clients detect them,
but this does not protect legacy writers from overwriting: mark that interval as
incomplete conflict protection and close it at cutover. Never silently break an
older client under the existing API contract.

On conflict preserve the draft and its base version, show the latest authorized
server values and require an explicit revised save. Do not replace the draft on
refetch or blindly retry using the newer version. On lost response, read current
state before deciding whether a save succeeded. Version the fields the user edits;
background thumbnail progress should not create false metadata-edit conflicts.
Unrelated settings/printer commands need their own domain contracts, not a universal
version wrapper. Apply the same conflict policy as their editable forms migrate.

### Deferred work

Stateless API processes and multi-instance backend readiness are deferred to a
separate backlog task. They are not a prerequisite, implementation step or success
criterion for this frontend migration. Preserve the existing storage, session and
JobEngine seams without adding deployment infrastructure here.

## Execution status

M2 has locally qualified implementation and acceptance, consolidated in its
[validation record](../frontend-m2-closure-validation.md) and parent matrix rows
1–5. Its prerequisite M1 is still open, so M2 is not formally closed. Local
acceptance, prerequisite readiness and final delivery are distinct states.

Follow the dependency graph in the table below. The closure sequence is
M0 → M1 → M2 → M3 → M4 → M5 → M6 → M7 → M8 → M9 → M10 → M11.
M0 is qualified by the [exact-base checkpoint](baseline-measurement.md#m0-acceptance):
501 original unit/package tests, 17 production functional cases, 200 startup
observations and bounded reproductions of the six failure families. M1 is the
next closure target. Parallel work may divide the active goal's bounded
review and verification tasks; it must not substitute a later goal for an
unfinished prerequisite. A goal closes only when its prerequisites and its own
acceptance and removal criteria are satisfied.

Work implemented ahead of this order is preserved with its actual qualification
results. It is not discarded, counted as a closed goal, or rerun without a
specific need. The qualified M5 refresh checkpoint and pending navigation work
remain recorded in their feature validation documents. With M0 qualified, only
M1 is active; later work waits for its stated prerequisites. Final delivery and
remote CI remain M11 work; no full-suite or performance improvement is claimed here.

## Ordered increments

Each increment must leave the application runnable. Dependency numbers identify
prerequisites, not permission to start parallel ownership systems. Use one active
branch/PR per agreed coherent objective under repository conventions; do not create
a branch for every row. Remove the superseded mechanism in its owning increment.

| Step                                 | Problem and intended outcome                                                                                                | Modules / contracts                                                                                                | Depends on | Acceptance / tests                                                                                                                                                                | Risk, rollback and removal                                                                                                                                                                                                                                    |
| ------------------------------------ | --------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ | ---------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M0 — Freeze evidence                 | Distinguish current failures from historical results; finish the review ledger for the implementation base.                 | All inventoried roots, supplied startup report, test harnesses, build/proxy.                                       | None       | Reproduce six concrete failure families, read existing assertions, run applicable baseline gates and comparable production timings. Record SHAs plus dirty-state provenance.      | Do not measure the installed image as changed source. Preserve unrelated edits. Remove no product mechanism.                                                                                                                                                  |
| M1 — Session and transport           | One remote freshness owner; old session responses cannot publish into a new session.                                        | HTTP JSON/actions/forms/blobs/streams, auth lifecycle, QueryClient, direct fetch clients, extension auth boundary. | M0         | Deferred headers/body, logout/login, same-account session change, old 401, abort and mutation acknowledgement tests.                                                              | Inventory non-Query consumers before deleting TTL/dedup. Temporary compatibility invalidation has named callers and an end date; delete it by M10. Roll back one coherent transport cutover, never restore known isolation defects.                           |
| M2 — URL and two modes               | Reproducible navigation with Everything / Multipart Sets only.                                                              | ModelBrowser, filter sidebar, saved views, return URLs, preferences, locale labels, CONTEXT.                       | M1         | Legacy URL/preference migration, malformed inputs, saved-view round trips, Back/Forward and independent Model access.                                                             | Canonicalize with replace, not a new history entry. Remove Organized/Parts only UI and client membership lists. Old API consumers remain compatible. Revert UI cutover if needed; keep data.                                                                  |
| M3 — Server browse                   | Globally ordered, authorized pages with stale-continuation detection.                                                       | Library read owners/API/schema/cursors, write-revision contract, OpenAPI, query options.                           | M2         | SQLite/Postgres order/filter/tie/null/permission/live cases; revision races and every writer; query/parameter scaling and latency budgets.                                        | New query/index/revision may add contention. Additive migrations only; autogenerate and test upgrade. Remove frontend mixed sorting and arbitrary group limits. Roll back consumer to previous endpoint only with the unresolved defects explicitly restored. |
| M4 — Mutations and edit conflicts    | Confirmed writes survive late reads; concurrent edits cannot silently overwrite.                                            | Library mutation owner, list/detail/count consumers; Model/Multipart/Document conditional-write contracts.         | M1, M3     | Refetch/continuation races, confirmed favourite removal, failures, double action, two editor sessions, lost acknowledgement, revoked permission.                                  | Remove per-card StarOverride and full-cache rollback snapshots. Revert UI/version enforcement together during a compatibility window; retain additive columns/migrations. Do not claim protection while bypasses remain.                                      |
| M5 — Coherent history                | Back restores the right list and nested scroll position without mixed folder headings/cards.                                | Library route composition, snapshot presentation, history restoration, outliner, navigation shim.                  | M2–M4      | Real browser Back/Forward, rapid A→B→C, cache eviction, deleted anchor, mobile layout, permission/session change.                                                                 | Bounded restoration; no endless fetch-until-found or retained private DOM. Remove popstate mirrors and ignored scroll/prefetch promises. Revert restoration owner as a unit.                                                                                  |
| M6 — Startup and assets              | Visible content gets bandwidth first; mounted images remain valid under pressure.                                           | Asset lifecycle, viewport admission, ModelCard, outliner restore/prefetch, deferred viewers/forms.                 | M1, M5     | Request admission/cancellation/leases/bytes; first completed restore batch visible; actual decoded-image timing; Shift selection and DnD after append.                            | Tune a small concurrency limit from measurements; measure API contention too. Remove eager all-card fetch and unsafe entry-count-only eviction. Preserve session fencing and lazy viewer chunks on rollback.                                                  |
| M7 — Async workflows                 | One remote owner per Inbox/Job read; no reconnect after a connection is disposed.                                           | Mnbox/nav badge, task center, uploads/archive review, events, printers/fleet/queues.                               | M1, M4     | Pending-ticket cleanup, out-of-order events, reconnect resync, offline polling, visibility changes, printer switch, upload retry/cancel.                                          | Keep local byte progress and command state distinct from durable Jobs. Remove duplicated migrated timers; retain justified completion-chained process polling. Roll back each workflow, not all event consumers at once.                                      |
| M8 — Remaining library surfaces      | Shared reads and consistent editing/recovery across detail, documents, builds, search, similarity, profiles and statistics. | Feature queries, forms, filters, selection and viewer cancellation.                                                | M1, M4, M7 | Draft preservation, validation errors, read permissions, failed continuation, public contracts for every route; headline browser flow for new conflict UI.                        | No generic form/repository framework. Remove effect-owned remote result copies and catch-to-empty success. Migrate one workflow plus all its consumers at a time.                                                                                             |
| M9 — Administration and entry routes | Settings/process workflows retain capability and safety rules with explicit state ownership.                                | Storage/backup/sources, provider setup, users/tokens, auth/setup, public share, notifications.                     | M1, M7, M8 | Credential omission, permission changes, long Job recovery, restricted operations, public/private navigation and form conflict contracts.                                         | Do not generalize destructive commands into generic CRUD. Remove migrated polling/cache copies only after recovery tests. Preserve backend safety gates and original form validation when reverting.                                                          |
| M10 — Packages and platform          | Finish ownership boundaries across all first-party files; keep extension and static delivery compatible.                    | UI/domain packages, app adapters, extension capture, localization, PWA, Vite/nginx, lint/type/test configs.        | M2–M9      | Import-direction/cycle checks; package consumers, extension contract suites; chunk failure/redeploy, EN/ES, Cache Storage failure, offline shell and auth isolation.              | Do not rename packages solely for aesthetics. Remove compatibility barrels/shims/invalidation bridges after final caller migrates. Locale splitting/virtualization needs separate measured evidence. Roll back deployment changes independently.              |
| M11 — Close qualification            | Demonstrate observable correctness, ownership and performance separately.                                                   | Full ledger, matrices, architecture/contributor docs, PR checks and measurement report.                            | M0–M10     | No unexplained missing required behaviour; every first-party source/config owner reviewed; relevant gates green for final SHA; comparable timings and remaining limits published. | Smaller files and green suites are not performance evidence. Leave unresolved findings explicit; never mark migration complete while parallel owners remain.                                                                                                  |

Before each implementation increment, expand the starter matrix into every distinct
happy/edge/error behaviour for its concrete contracts, then inspect existing tests
and write missing regressions. Tests for integrity/session isolation precede code.
Do not port the earlier unfinished local prototype wholesale: it is outside this
documentation change, is not a qualified baseline and may overlap existing work.
Any cleanup must identify ownership against the preserved pre-task state.

## Completion criteria

Correctness: all documented failures have a named observable regression and a
passing relevant run; frontend contracts agree with backend membership/order and
conditional-write semantics. Historical reproduction is not a current pass.

Maintainability: every remote read has one named owner; URL-derived values are not
mirrored in local state; migrated mutations declare affected read models; temporary
bridges are gone; contributor rules are enforced by tests/lint. Measure eliminated
duplicate requests/timers and forbidden edges, not line count.

Performance: publish before/after distributions using the same corpus, production
build mode, proxy, browser, viewport and cache state. Verify actual interactions
and decoded thumbnails; report request counts/queue time, API timings, JS/render
cost and memory separately. No improvement is claimed by this plan.

Remaining decisions are implementation details to resolve from M0 evidence: exact
mixed-sort normalization, revision granularity, cache memory budget, concurrency
limit and conditional-write rollout compatibility. Escalate only a demonstrated
product/deployment trade-off; none licenses introducing a new framework by default.

## Execution qualification

The consolidated plan preserves the settled decisions above. Partial changes in
three isolated implementation branches are candidates for review, not accepted
results. Preserve the original dirty checkout; do not revert an earlier prototype
without identifying the owner of each change. The planning branch remains local.

Implementation base: `710e4eb7aafe05642693d6ff2696146e042dca53`. Keep
startup improvements already committed on that base. Reconcile the historical
review ledger against this base before claiming complete review.

Every goal M0–M11 needs its own observable acceptance evidence. No goal closes
from a worker report alone: inspect the changes and qualify the integrated result.
One writer owns each path. Two repeated failures with the same approach require
diagnosis before another attempt. Group implementation into state foundation,
library/navigation contracts, and feature ownership; no branch per small step.

Browse and conditional-edit wire contracts are documented with the owning backend
validation matrix. Start thumbnail admission at four concurrent downloads and
change that limit only from comparable measurements. Restoration is bounded by
the pages previously loaded by the history entry; it never searches indefinitely.
The final matrix retains every distinct behaviour from the original 56-row plan
and the supplied 20-row revision. A missing row is not waived by a passing suite.
