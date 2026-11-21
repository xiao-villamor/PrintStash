---
name: backend-engineering
description: Plan, implement, or review PrintStash FastAPI, SQLModel, storage, configuration, printer providers, durable work, read projections, and backend contracts.
---

# Backend engineering

Read [implementation principles](../implementation-principles/SKILL.md) and
[codebase architecture](../codebase-architecture/SKILL.md) before placing a
capability. Read the [backend reference](references/backend.md) for composition,
configuration, current owners, and verification.

## Boundary and component roles

| Role | PrintStash placement and responsibility |
| --- | --- |
| HTTP adapter | `backend/app/api/v1/` parses the request and composes existing dependencies. It translates the result once for HTTP. |
| Wire contract | `backend/app/schemas/` describes public input and output. Validate external data at entry and reuse the owning response projection. |
| Domain value or policy | A capability owns intrinsic validity, closed states, and pure decisions over resolved facts. Portable rules with real consumers can live in `printstash-core`. |
| Command | A capability handler owns one meaningful write intent, authority checks, transaction, and durable result. HTTP and worker paths call the same owner when semantics match. |
| Query or read projection | The owner shapes a bounded result for a caller. Library Model composition belongs in `modules/library/model_views`. |
| Persistence | `db/models/` declares SQLModel tables. The owning capability chooses predicates, relationships, and commands. Use shared `db/` mechanisms for session, transaction, and visibility rules. |
| Port and adapter | A contract states what an owner needs from an independently varying dependency. An adapter translates the storage, protocol, or provider. |
| Job Definition and Work Source | A capability records its durable intent and owns Job steps, recovery, and subject transitions. |
| Composition and runtime | `bootstrap/` wires resources and owns startup and shutdown. `runtime/` supplies execution and transport without absorbing feature rules. |

Classify proposed code by role and lifetime before naming or locating it.
Separate roles when one object would otherwise combine routing, policy,
persistence, and provider decisions. Preserve existing public seams. Add a
protocol or shared package when the boundary needs one.

Keep database rows behind the persistence owner. Transfer typed results and
IDs to callers. Keep ORM, HTTP, engine, and provider SDK types out of
`printstash-core` public interfaces. Inject configuration, user scope, time, and
other inputs at the composition or operation boundary where they are chosen.
Child collaborators borrow the session or capability they need from that owner.

## Required references

Read all applicable rows before planning, editing, or reviewing the surface.

| When changing | Read |
| --- | --- |
| Type, Service, capability door, port, adapter, dependency wiring, or lifecycle | [Contracts and composition](references/contracts-composition.md) |
| Entity identity, editable payload, row mapping, transaction, multi-record write, or external effect | [Identity and writes](references/identity-writes.md) |
| Schema, migration, stored enum, relationships, SQLite or PostgreSQL behavior | [Database](references/database.md) and [Identity and writes](references/identity-writes.md) |
| Lookup, predicate, read projection, cursor, batching, or sorting | [Reads and ordering](references/reads-ordering.md) |
| Error translation, reconciliation failure, diagnostics, events, or metrics | [Failures and observability](references/failures-observability.md) |
| Jobs, Work Sources, lanes, Derivatives, or recipe versions | [Background work](references/background-work.md) and [Identity and writes](references/identity-writes.md#work-across-commits) |
| Storage ownership, cleanup, backup, or migration | [Storage data safety](../../../docs/storage-data-safety.md) and the owning capability |
| Printer protocols, capabilities, work status, or emulators | [Providers](references/providers.md) |
| Pending Imports, provenance, URL capture, or provider connections | [Capture engineering](../capture-engineering/SKILL.md) |
| API requests, responses, errors, or events consumed by the UI | [Frontend-backend integration](../frontend-backend-integration/SKILL.md) |

## Build or extend a feature

1. **Trace the entry and every caller.** Find the owner of the rule before
   accepting a file as its home. Read the current dependencies, scope
   predicates, data, and tests. Identify persisted or external facts that
   change independently.
2. **Model the contract.** Declare accepted values, target identity,
   authority, states, results, and bounded failure shape. Centralize rules
   shared by create and update. Let the backend enforce every rule exposed
   through UI controls and permissions. Define whether missing state is
   allowed, refused, or unavailable.
3. **Shape the read.** Decide its scope, freshness, filters, order, bounds,
   and exact output. Reuse central projections where they fit. Batch joins
   and data assembly under the capability, so callers receive what they
   need without a per-row request. Keep large media and downloads one-shot.
4. **Choose a write strategy.** For a local atomic act, validate the complete
   target set under current scope and stage one transaction. For I/O across
   commits, persist the intent and use the existing Job/reconciliation seam.
   Carry stable evidence across suspension and claim the correct version on
   reentry. Document retry and cancellation semantics before wiring events.
5. **Compose and verify.** Wire one owner through the normal entry point. Use
   [create-tests](../create-tests/SKILL.md) for the matrix and
   [run-tests](../run-tests/SKILL.md) for verification. Prove old data and
   supported deployments where contract changes reach those paths.

## Completion review

Look for policy in routers or generic runtime, independent child commits
inside an atomic action, IDs used as authorization, time-of-check/time-of-use
gaps, unbounded ORM reads, missing unique tiebreaks, missing live/trashed scope,
and queries that treat errors as empty. Check safe event timing, invalidation,
and durable recovery. Confirm failures are observable without exposing
credentials or uploaded content. Account for OpenAPI and generated contract
diffs when changing a wire shape.
