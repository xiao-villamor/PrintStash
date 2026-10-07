# M5 — Library navigation acceptance

Status: local acceptance qualified; M0–M4 prerequisites are closed. Final delivery
and remote CI remain M11 work. This record consolidates the implemented history,
snapshot and restoration contracts; it does not expand the migration scope.

## Ownership and removals

React Router owns URL and history. `features/library/navigation.tsx` provides
native item links with an encoded Library fallback and an immediate-origin Back
link. `navigation-state.ts` owns a session-scoped, bounded registry of 64 history
entries. Entries contain scroll/anchor/page-count metadata, never remote records.
`reading-position.ts` owns the actual main/list containers and reconstruction up
to the previously loaded page counts. Query owns entity pages. ModelBrowser
publishes one settled snapshot, including breadcrumbs and action origins; private
retirement overrides stale presentation.

The navigation shim now exposes only implemented push, replace, Back and Forward
operations. Removed: ignored scroll arguments, no-op route refresh/prefetch,
card hover handlers whose only effect was calling that no-op, and comments
claiming server-rendered prefetch. Earlier M2/M5 increments removed local URL
mirrors and animated Library entrances that changed measured reading geometry.
Real Query prefetch remains at its existing owner. No router/framework/runtime or
backend contract was added in this milestone.

## Coverage matrix index

The detailed matrices were prepared before their tests and assessed against named
assertions. This index groups those matrices; it does not replace their individual
behaviour rows.

| Contract | Assertion-level matrix |
| --- | --- |
| Router operations, item navigation, delete destinations, URL consumers and native gestures | [Navigation API](library-navigation-api-validation.md), 27 rows |
| Source identity, nested offsets, bounded reconstruction, missing anchors, confirmed favorite neighbor, explicit refresh | [Library navigation](library-navigation-validation.md) |
| Coherent refresh, lookup/children/model coordination, failure retention and retirement | [Library refresh](library-refresh-validation.md) |
| Rapid A→B→C, logout, permission revalidation, repeated URL entries, real paged detail Back | [Snapshot browser](library-snapshot-browser-validation.md), five rows |
| Plan-level acceptance | [Parent matrix](frontend-architecture/validation.md), rows32–36 |

## Browser evidence

The combined Chromium invocation passed all 15 established navigation cases and
three snapshot cases. Its new repeated-URL probe failed: it compared a bookmark
recorded before a menu interaction that can move focus/scroll. The replacement
creates the second visit through actual detail and the header Library link. Its
first run identified a fixture mismatch between an explicit sort and the header's
stored-preference destination. With the existing default sort in both visits,
the test passed in 8.0 seconds (10.8 seconds total), including distinct Router keys
and each entry's geometry through Back/Forward. No production change was made
for either test-harness correction. The original failed runs remain explicit;
there is no claim that the initial combined invocation was wholly green.

Real FastAPI/SQLite acceptance passed both desktop grid and mobile list cases:
13.9 and10.3 seconds, 60.0 seconds total including startup. Each seeds a unique
502-Model collection through the existing scale factory, appends the second page
using Load more, opens a real Model, and returns through UI Back and browser
Forward/Back. Exact URL, entry identity and container geometry are asserted.
Each test deletes its fixture subtree. No mocked API participates in this lane.

The mock browser cases cover real Query garbage collection, removed/stale
anchors, no requests beyond previously visited pages, Model/Multipart return,
grid/list desktop/mobile, confirmed favorite removal, explicit refresh, and
private retirement before logout/access acknowledgement. Response gates control
HTTP completion only; tests do not mutate private caches or replace rendered DOM.

## Final checks

The final affected Vitest gate passed **368 tests across 10 files in99.78 seconds**:
router, item links, entry metadata, reading restoration, cards, Model detail,
Library search, search results, the complete grid mirror and full suite hygiene.
The three native modified-click cases emit JSDOM's expected unsupported-document
navigation diagnostic; there were no failed tests or unhandled errors.
App/UI/domain typechecking, full lint and formatting (760 files) passed.
Final browser-spec lint/format passed for all three touched specs. The affected
Settings URL case passed separately in5.29 seconds (153 unrelated cases were
name-filter deselected; no skips were added). Vite production build passed in
1.58 seconds, retaining the existing large-chunk warning. Whitespace checks passed.

## Limits and rollback

Correctness is qualified by observable navigation and geometry. Maintainability
comes from one bounded metadata owner and removal of APIs promising effects they
did not implement. Test durations are not performance measurements. Comparable
startup/asset measurements remain M6/M11; no speedup or whole-suite CI claim is made.

The metadata registry is intentionally tab-local and bounded. Unknown/new-tab
origins use the safe Library URL, and an unavailable anchor gives an explicit
reset after bounded reconstruction. No persisted private Query cache is added.
Revert the reading owner and its link/snapshot integrations together to roll back
restoration; this changes no stored user data or schema. Keep URL normalization
and private-session fencing intact. The original dirty checkout and ahead-of-order
M7/M9 work remain preserved.
