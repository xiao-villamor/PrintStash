---
name: create-tests
description: Design, write, change, or audit PrintStash tests. Build the required coverage matrix, characterize behavior, choose test tiers, and use the project harness and factories.
---

# Create tests

Decide coverage and tier separately. Enumerate each observable behavior that
needs a proof, then choose the least costly real boundary that establishes it.
Use one test per behavior, parameterized when several inputs prove the same
invariant. Every new feature needs one e2e test for its headline capability.

Read the [test-design policy](references/testing.md) before changing a test.
It owns the matrix format, full behavior sweep, tier rules, and coverage
floors. Load each applicable runtime reference from it. Use
[run-tests](../run-tests/SKILL.md) for local and CI execution.

## Plan from intent and walk the implementation

1. Read the request, acceptance criteria, domain glossary, provider or
   operator guide, and any published contracts the change reaches. Enumerate
   the positive path and the distinct permissions, boundaries, transitions,
   recovery paths, and persistence effects.
2. Walk the current owner and the proposed diff. Decisions in predicates,
   ordering, short circuits, exception handlers, and loops reveal scenarios
   the prose may omit. Map each path to a behavior a real caller can observe.
   A complexity report can direct this sweep. Counts do not determine how
   many tests to write.
3. Take the **expected outcome** from intent, an established invariant, or
   a defensible product decision. When there is a discrepancy, mark it in
   the matrix and resolve it. The implementation can identify an edge and
   still implement that edge incorrectly.
4. Write the matrix before editing. Mark missing rows `❌ missing` and
   inapplicable ones `⏭️ N/A — <reason>`. Use
   [the required columns](references/testing.md#the-coverage-matrix--mandatory-for-every-feature).
   In audit mode supply the missing rows as findings. In implementation mode
   pick a tier and write or extend the proof for each row.

## Characterize behavior before a refactor

For undocumented behavior the task must preserve, capture current observable
results through production-facing calls. Run those tests on the original code
before changing it. An apparent bug is a separate contract decision. Do not
quietly correct it inside a behavior-preserving refactor.

| Change shape | Cases to walk |
| --- | --- |
| Loop, batch, or page | Empty, one, exact limit, one past the limit, final partial page |
| Predicate or guard | Kept and rejected values at the exact edge, plus failed required lookup |
| Sort, merge, or selection | Tied product keys, absent optional key, reversed input, tie across a page boundary |
| Time window or retry | Just before and exactly at the boundary, interrupted work, delayed or duplicate completion |
| Mapping or parser | Each closed case, optional absent versus explicit value, malformed foreign input |
| Transaction or adapter | Failure before and after the durable boundary, changed authority, subsequent retry |

## Apply the persistence and integration sweep

- **Round-trip meaning.** Write through the owning operation and read the
  saved result. Prove explicit absence stays absent where it matters, and
  that queries place missing data according to the product order. Use rows
  tied on every primary sort key to prove the unique tiebreak.
- **Preserve mutation evidence.** When an edit promises a version, timestamp,
  event, or idempotent result, assert a real change advances the intended
  evidence and a no-op keeps it stable where specified. Check neighboring
  rows and other scopes remain unchanged.
- **Prove atomicity and recovery.** Where a write stages several effects,
  drive a failure after partial progress. Check actual rollback or durable
  recovery before retry, then check replay, cancellation, and late results
  where they can change outcomes. Drive the stale version and wrong scope
  explicitly. For security and data-integrity fixes, run the failing
  regression first using the [TDD loop](../tdd/SKILL.md).
- **Exercise compatibility.** For schema changes, upgrade representative old
  data. For wire changes, review the OpenAPI or generated contract diff and
  prove the affected producer/consumer combinations under supported versions.
  For provider changes, exercise the contract through its emulator or required
  real service.
- **Prove live behavior in flight.** Open the affected subscriber or screen
  before changing backend state. Observe the new read, receipt, or recovered
  Job through the normal event/reload path. A final snapshot alone misses a
  subscription that never refreshes.

## Pick the boundary and environment

| Behavior | Use |
| --- | --- |
| Pure value, transition policy, parser, or calculation | Unit. Keep the DB and network outside the test. |
| SQL, HTTP authorization and validation, composition, or persisted effects | Integration with real app seams and SQLite. Use PostgreSQL or another real resource when its semantics are the contract. |
| Provider, storage, or engine protocol | Contract with the normal client and contract-enforcing transport or real service. Use its defined failure controls. |
| Headline product behavior or recovery spanning several components | Backend e2e or real-backend Playwright according to the user entry point. |
| React state, rendering, or controls | Vitest with the existing React, Query, router, or HTTP harness. Use Playwright when browser behavior, persistence, or the full journey is the promise. |
| Packaging, dependencies, or other enforced static contracts | Existing repository guard or artifact verification. |

Tier says what is proved. Runtime and resources say where it was proved.
One behavior may run against SQLite and PostgreSQL, local and optional
storage, or inline and DBOS work engines when the contract spans them.
Record actual lane results without counting each environment as a new behavior.
Fast PR lanes cannot prove properties that require the Deep CI resources.
A mocked browser route cannot establish that a composed backend persisted data.

## Author deterministic tests

- Prefer the public product entry point for a transition you intend to prove.
  Use the established [factories and fixtures](references/tests/fixtures.md)
  to arrange backend state and obtain identity. Use a specialized existing
  fixture when testing a state no product caller could create. Keep the
  policy about row construction and visibility with the factory owner.
- Fix environment state at its narrow fixture or harness. Scope dependency
  overrides, clocks, auth, storage, caches, and concurrency controls to their
  test lifetime. Reset them on exit. An absence test must control the
  absence explicitly instead of inheriting the suite's usual setup.
- Synchronize on an observable condition. Drain the existing inline Job
  engine, use a controlled barrier for a precise race, or wait for an
  observable with a wall-clock deadline. On expiry report its last state.
  Avoid fixed sleeps, unbounded polling, and scheduler-dependent yield counts.
- Assert the read-back row, API response, bytes, rendered interaction, or
  contract-emulator receipt. Keep the real core of the behavior under test.
  Use the repository's boundary injection rules. Respect the independent
  constraints of a real database or provider.
- Review a changed snapshot or golden as a contract diff. Regenerate after
  confirming the change is intended. Keep assertions that catch the previous
  failure.

## Close the matrix

For each row, identify the exact test and which observable assertion proves
it. Replace planned statuses with evidence or an explicit inapplicability
reason. Report any missing proof as missing. Use coverage reports to find
additional paths, then return to the requirements to decide what those paths
should prove. Include the final matrix in the response and PR.

For guidance-only edits, validate the artifacts and run affected repository
guards. Add runtime tests when runtime behavior changes.
