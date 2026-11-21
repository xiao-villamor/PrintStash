---
name: frontend-backend-integration
description: >
  Design or change the boundary between the PrintStash frontend and backend, including API schemas, query keys, write receipts, events, editing, and Job recovery.
---

# Frontend-backend integration

Trace the operation from its React caller through its feature owner,
`frontend/src/lib/api/`, and the FastAPI router to the owning backend
capability. Use [backend engineering](../backend-engineering/SKILL.md) and
[frontend engineering](../frontend-engineering/SKILL.md) for each side.

| Contract | Verify |
| --- | --- |
| Read | Name the response, identity, scope, and consistency boundary. Include every response-shaping input in the query key. Forward cancellation and preserve the Library snapshot contract where it applies. An unavailable read remains distinguishable from an empty result. |
| Write | Capture the target, authority, and edit base with the intent. The backend revalidates scope and enforces the write invariant. Preserve the base while dirty, handle conflict and unknown outcomes explicitly, and update or invalidate the affected reads from a confirmed receipt. |
| Events and Jobs | Events inform the client that authoritative state may have changed. Reconcile through the owning read and recover durable Jobs after reconnect or reload. Keep presentation lifetime independent from durable work. |
| Validation | State the rule at its domain owner, enforce it on the backend, and reflect the same bounds in the UI. Carry structured IDs, values, and failure codes across the wire. Format localized text at presentation. |
| Compatibility | Check scripts and the browser extension alongside the web client. Prefer additive API changes. Read the diff of the OpenAPI snapshot for intentional changes and check generated printer contracts when affected. |

1. Read the contract from both directions. Locate the owning schema and every
   consumer before changing shape, ordering, authority, or lifecycle.
2. Change the backend and its consumers together. Keep fetches in the existing
   transport and business rules in the owning capability. Use server-advertised
   capabilities for provider gating.
3. Use [create-tests](../create-tests/SKILL.md) for observable behavior at each
   seam. Where applicable prove API validation, authorization, conflict, and
   failure behavior. Exercise the real Query/router/transport harness and a
   real-backend browser flow for the headline capability.

Done when the request, response, and failure shapes agree through each caller
and the feature can recover from interrupted or ambiguous work.
