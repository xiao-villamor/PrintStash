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
