# Frontend dependency boundaries

Implementation checkpoint for M10. The requirements below come from the dependency directions in [frontend architecture](../architecture/frontend.md), not existing import counts. The first 20 rows were recorded before implementation; the closing assessment below identifies the assertions now in place.

## Behaviour matrix

| #   | Behaviour (test name)                          | Category | Precondition / input                                             | Observable outcome asserted                              | Tier          | Status                                                                                         |
| --- | ---------------------------------------------- | -------- | ---------------------------------------------------------------- | -------------------------------------------------------- | ------------- | ---------------------------------------------------------------------------------------------- |
| 1   | accepts permitted dependency directions        | Happy    | Package, endpoint, feature compositions                          | No violations                                            | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::accepts permitted dependency directions`         |
| 2   | resolves first-party module identities         | Happy    | Relative, alias, index, workspace exact/wildcard exports         | Canonical targets                                        | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::resolves first-party module identities`          |
| 3   | discovers supported import syntax              | Edge     | Static, side-effect, re-export, literal dynamic, TS import type  | Source-located edges                                     | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::discovers supported import syntax`               |
| 4   | distinguishes explicit type-only edges         | Edge     | Declaration-level, specifier-level, mixed imports/exports        | Correct classifications                                  | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::distinguishes explicit type-only edges`          |
| 5   | excludes nonproduction graph roots             | Edge     | Tests, support, generated, dependencies, artifacts               | Excluded nodes absent                                    | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::excludes nonproduction graph roots`              |
| 6   | rejects package boundary bypasses              | Error    | Relative or alias package to app; domain to UI                   | Boundary diagnostic                                      | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects package boundary bypasses`               |
| 7   | rejects undeclared workspace entry points      | Error    | Unexported workspace subpath                                     | Resolution diagnostic                                    | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects undeclared workspace entry points`       |
| 8   | rejects upward transport dependencies          | Error    | Endpoint/transport to React, Query, UI, route, feature           | Boundary diagnostic                                      | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects upward transport dependencies`           |
| 9   | rejects infrastructure feature dependencies    | Error    | Session/auth-store/events to feature                             | Boundary diagnostic                                      | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects infrastructure feature dependencies`     |
| 10  | rejects private feature consumption            | Error    | Outside or sibling to private feature module                     | Boundary diagnostic                                      | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects private feature consumption`             |
| 11  | rejects feature route composition dependencies | Error    | Feature to page/router/bootstrap                                 | Boundary diagnostic                                      | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects feature route composition dependencies`  |
| 12  | rejects upward shared-contract dependencies    | Error    | DTO to implementation                                            | Boundary diagnostic                                      | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects upward shared-contract dependencies`     |
| 13  | reports runtime strongly connected components  | Error    | Runtime cycle, lazy cycle, self-cycle                            | Exact cycle members and edges                            | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::reports runtime strongly connected components`   |
| 14  | reports cycles involving type-only edges       | Error    | Cycle closed by type import                                      | Separate type-involving SCC                              | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::reports cycles involving type-only edges`        |
| 15  | reports unresolved computed imports            | Error    | Dynamic expression                                               | Source-located diagnostic                                | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::reports unresolved computed imports`             |
| 16  | rejects unresolved first-party imports         | Error    | Missing alias or relative target                                 | Resolution diagnostic                                    | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects unresolved first-party imports`          |
| 17  | rejects parser failures                        | Error    | Invalid source                                                   | Parse diagnostic                                         | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects parser failures`                         |
| 18  | limits an exception to its declared edge       | Edge     | Exact edge versus added symbol or changed target                 | Only declared edge accepted                              | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::limits an exception to its declared edge`        |
| 19  | rejects stale exceptions                       | Error    | Exception edge removed                                           | Stale-exception diagnostic                               | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects stale exceptions`                        |
| 20  | discovers literal worker module loads          | Edge     | Worker(new URL(..., import.meta.url))                            | Canonical worker edge                                    | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::discovers literal worker module loads`           |
| 21  | enforces the production repository graph       | Happy    | Current app and both package source roots                        | No diagnostics, including the real worker protocol cycle | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::enforces the production repository graph`        |
| 22  | resolves geometry through the worker           | Happy    | Shared ready reply consumed through the existing client API      | Expected geometry returned                               | Frontend unit | ✅ `src/lib/__tests__/gcode-worker-client.test.ts::resolves geometry through the worker`       |
| 23  | returns a recognizable segment-limit error     | Error    | Shared limit reply                                               | Recognizable limit rejection                             | Frontend unit | ✅ `src/lib/__tests__/gcode-worker-client.test.ts::returns a recognizable segment-limit error` |
| 24  | reports invalid input after worker cleanup     | Error    | Shared invalid reply                                             | Recognizable invalid rejection                           | Frontend unit | ✅ `src/lib/__tests__/gcode-worker-client.test.ts::reports invalid input after worker cleanup` |
| 25  | rejects production test dependencies           | Error    | Production imports test-support, test fixture or test file       | Source-located boundary diagnostic                       | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects production test dependencies`            |
| 26  | rejects undisclosed workspace packages         | Error    | A newly discovered package is absent from the source-root policy | Explicit workspace-policy error                          | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects undisclosed workspace packages`          |
| 27  | rejects changed workspace source patterns      | Error    | Workspace manifest declares an unhandled source pattern          | Explicit source-root error                               | Frontend unit | ✅ `tests/repo/dependency-boundaries.test.ts::rejects changed workspace source patterns`       |

## Enforced directions

`frontend/tests/repo/dependency-boundaries.test.ts` runs the Oxc-based checker in
`frontend/scripts/dependency-boundaries.ts` against the real repository. It also
checks small source fixtures for resolution, diagnostics and cycles. Existing
Vitest discovery includes this repository test; no build or lint configuration
changes are needed.

- UI and domain packages cannot import app source. Domain cannot import the UI
  package or React/Query frameworks. Cross-package consumers use declared exports.
- Endpoint clients can consume other endpoint modules, transport, shared DTOs,
  auth-store/session contracts and API errors. Transport cannot import endpoint
  clients, React, Query policy, routes, components or features.
- Auth-store, session transport and event infrastructure cannot import features
  or application composition. The event-ticket endpoint is a permitted endpoint
  dependency. `lib/queries.ts` is legacy query composition, not transport; it may
  delegate reads to feature public modules.
- Features cannot import pages, the router or bootstrap. Outside consumers,
  including sibling features, use the explicit public-module manifest. Same-feature
  internals remain free to compose one another. Shared DTOs cannot depend on
  application implementation.

The explicit `FEATURE_PUBLIC_MODULES` manifest in the checker is authoritative.
A new file is private by default; extending it is an API decision, not an exemption
based on directory placement. The M10 review adds printer `settings-edit` (captured
conditional command/review state), `settings-review` (the shared review UI consumed
by list/detail forms), and Library `builds` (Build query/command owner consumed by
the Build route). Their exported contracts already serve external consumers;
registering them preserves the boundary without introducing forwarding barrels.

M10 removed the transport-invalidation exception: endpoint clients and transport
cannot import Query policy. The exception engine remains covered with synthetic
fixtures; the production exception list is empty. Taxonomy commands and Library
metadata publication are explicit public interfaces because app compositions use
their effects without exposing private feature internals. A stale exception still
fails the gate.

## Resolution and limits

The graph includes authored production modules under `src`, `packages/ui/src`
and `packages/domain/src`. Tests, test-support, generated sources, declarations,
dependencies and build artifacts are excluded as graph nodes. Production imports
of tests or support fixtures fail instead of becoming invisible leaves. Newly
discovered workspace packages or changed workspace source patterns fail until
this explicit source-root policy is updated. Generated/data/CSS
imports remain resolved leaves. Aliases come from TypeScript paths; workspace
entry points come from package export manifests, including wildcard exports.
The currently configured export targets are strings; this checker deliberately
does not implement a generic Node conditional-export resolver.

Static imports, side effects, re-exports, explicit type imports, literal dynamic
imports, require calls and module-relative Worker/SharedWorker URLs become edges.
Mixed type/value imports remain runtime edges. Unannotated imports are conservatively
runtime edges even if TypeScript might erase them. Unresolved first-party imports,
computed import/worker paths and parser failures fail with source locations.

Runtime SCCs are reported separately from all-edge SCCs involving explicit types.
The worker URL edge revealed a type cycle: the G-code worker imported its reply
contract from the client that created it. `gcode-worker-protocol.ts` now owns that
shared type; the client preserves its existing type export. No runtime behavior
or worker loading changes.

This gate establishes dependency directions, not complete feature ownership or
runtime behavior. Browser storage access in domain preference helpers is still
an explicit legacy portability concern; imports alone cannot detect it. Generated
modules and platform configuration are outside this production-code graph.

## Verified checkpoint

Focused Vitest: **85 passed** across dependency boundaries (73), repository
suite hygiene (4), and G-code worker client (8). Targeted oxlint reports no
warnings or errors; the app and both workspace packages pass typechecking.
The checker found no remaining runtime or type-involving cycle after the shared
worker-protocol extraction. Browser behavior was unchanged by that type-only move.
