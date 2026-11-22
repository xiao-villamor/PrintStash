---
name: codebase-architecture
description: >
  Locate the owning PrintStash module, select a frontend or backend boundary, or assess whether behavior belongs in a shared package.
---

# PrintStash architecture

Use this map for placement. Read [codebase-design](../codebase-design/SKILL.md)
and [implementation-principles](../implementation-principles/SKILL.md) when
changing a seam.

| Surface | Ownership |
| --- | --- |
| `backend/app/` | FastAPI adapters, capability modules, SQLModel mappings, dependency wiring, and process runtime. See [backend engineering](../backend-engineering/SKILL.md). |
| `backend/packages/printstash-core/` | Portable domain behavior and printer protocols. Application adapters supply persistence, authority, or execution. |
| `frontend/src/` | Routes compose feature public interfaces and present their state. Transport lives under `src/lib/api/` and feature owners control reads and writes. See [frontend engineering](../frontend-engineering/SKILL.md). |
| `frontend/packages/` | UI primitives and React-independent shared domain code. Check consumers and allowed dependency directions before promoting feature code. |
| `browser-extension/` | Browser capture surfaces and transport to PrintStash. See [capture engineering](../capture-engineering/SKILL.md). |

For a boundary change inspect the executable rules in
[backend architecture tests](../../../backend/tests/repo/test_architecture.py)
and [frontend dependency boundaries](../../../frontend/scripts/dependency-boundaries.ts).
Identify each caller and where authority, persistence, and runtime wiring
live. The placement is settled when the owning seam can enforce its contract,
the consumers use the public interface, and the architecture checks agree.

## Trace actual placement before restructuring

1. Start at the production caller: router, route component, worker,
   extension entry point, or exported core API. Follow the concrete
   dependency path to the rule it invokes. Check tests for any supported
   call path invisible from that first caller.
2. Separate product policy, projection, adapter, and composition. Product
   rules belong to the capability with authority over the facts. A read may
   belong alongside several existing reads with shared semantics. A
   persistence table or HTTP object can represent the facts without owning
   the policy.
3. Choose the smallest boundary that solves the maintenance or dependency
   problem. A subdirectory need not become a new protocol. General-purpose
   code stays close to its real consumers until it needs shared ownership.
4. Verify runtime and type-only imports. Check optional features, transitive
   dependencies, and public entry points. Automated boundary checks
   cover ownership but do not establish a feature's correctness.
5. Confirm the test and composition map: which fixture constructs the owner,
   which adapters are real in each lane, and which production composition
   root wires it. When moving a seam, move its dependent tests, resources,
   public guidance, and wiring with it.

## Concrete traces worth checking

- **Backend writes:** HTTP dependencies establish authority, the capability
  owns the write, and `app/db/` supplies shared persistence mechanics.
  Trace workers to the same capability where their operation has the same
  meaning.
- **Durable work:** definition and source belong to the feature, Jobs and
  Fences record state in the database, bootstrap wires them, and the
  engine adapter executes. Events and websocket transports have separate
  roles.
- **Frontend:** route and page compose the public feature interface, which
  owns its data flows and uses the HTTP client. Shared UI and domain
  packages expose independent contracts. Inspect
  `frontend/src/test-support/` for the composed client harness.
