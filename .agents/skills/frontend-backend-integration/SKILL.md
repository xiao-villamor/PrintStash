---
name: frontend-backend-integration
description: Design or change the PrintStash frontend and backend contract, including maintained reads, one-shot fetches, conditional writes, sessions, events, and durable Job recovery.
---

# Frontend-backend integration

Follow both [backend engineering](../backend-engineering/SKILL.md) and
[frontend engineering](../frontend-engineering/SKILL.md). Trace each operation
from the product surface to the owning feature, the existing client in
`frontend/src/lib/api/`, HTTP, then the backend capability. Expose one
authoritative operation per intent. Reuse existing transport and
feature-owned Query entry points. Include browser-extension and script
consumers when a wire contract changes.

## Three kinds of integration

| Kind | When to use it | Lifecycle |
| --- | --- | --- |
| Maintained read | The rendered region should stay current while the user is there | Feature query with response shaping keys, scope, cancellation, explicit available/loading/unavailable states, and invalidation |
| One-shot fetch | Preview bytes, export, download, or a point-in-time value | The initiating owner captures the input. Complete once against that scope and discard late results from superseded inputs. |
| Write | One submitted user intent or administrative action | Capture authority and any edit version. Apply a confirmed receipt, reject stale intent, or reconcile an unknown result. A durable Job owns subsequent work. |

## Maintained reads

- Define one independently coherent content region. Declare all filters,
  sorting, target, and other inputs that shape its response in its query
  key. Account for session or authority by the established isolation and
  reset mechanism. Forward cancellation through the full transport path.
- Keep stable reference separate from the snapshot it selects. A destination
  receives the ID and reads current truth. Server-provided IDs and revision
  tokens stay in their existing families. Never treat a cached snapshot
  from another session or scope as current.
- Use the existing Library browse and authority paths under
  `frontend/src/features/library/` for stable presentations. Respect
  scope, cursor and revision binding. Preserve a previous read only when
  the owner can establish that it remains authorized.
- State what changes the read: writes, projection revisions, permission
  changes, Job completion, time, reconnect, or explicit refresh. Put
  invalidation at the owning feature. Distinguish an event hint from
  committed state. Coalesce where the owner already supports it and
  recover when a client missed delivery.
- Render confirmed empty, initial loading, unavailable, and available data
  explicitly. Retry a read through its normal query. Keep ordering and
  joins in the backend projection for data beyond the loaded window.

## One-shot fetches

Capture all inputs before starting. Bound the request lifetime by the
initiating owner and pass the appropriate signal through the API client.
On completion verify that the same target and session still own the action.
Ignore cancellation or superseded results without changing another draft
or destination. A permission or transport error remains an explicit
failure. Emit downloads, object URLs, and other presentation effects only
after the operation confirms the right result. Release temporary handles
when their owner ends. Keep large bytes out of the maintained read cache.

## Conditional writes and Jobs

1. Capture one complete intent at its admission point, including target,
   payload, authority, and original edit base where the contract uses one.
   Submit it through the feature's mutation path. Do not use a caller loop
   to simulate a multi-record atomic write.
2. The backend re-resolves authority, validates the target and input, and
   enforces the precondition during the write. Client-side gating uses the
   same specified rule or advertised capability. The backend must refuse
   invalid script, extension, and browser callers alike.
3. A confirmed receipt identifies what changed and which reads need
   invalidation or update. Match versions before applying a cache change.
   A conflict or unknown result preserves the original draft and enters
   the explicit review or reconcile path. A response from a previous
   session must not update current UI state.
4. Identify the owner of any effect beyond HTTP completion. A Job receipt
   confirms admission. Query the Job to verify success, refusal or recovery.
   Handle reconnect, reload, cancellation, and retry at the owning seam.
   Do not add a new polling loop when the existing event and requery path
   already satisfies the lifecycle.
5. Distinguish read unavailability, input rejection, write conflict, and
   durable work failure. Present them through their existing channels.
   Use `frontend/src/lib/errors.ts` for the API error path. Keep
   diagnostic detail at the boundary that owns it.

## Verify both sides

Trace producer schema, transport, consumer, route, and state owner before
editing. Prefer additive wire changes. Review the OpenAPI snapshot and
generated printer contract diff where applicable. Use
[create-tests](../create-tests/SKILL.md) to cover authorization,
accepted input and refusal, stale base, retries, session change, and
missed events in the appropriate tiers. Exercise the real Query, router,
and transport harness and the real-backend browser flow for a headline
UI capability. Completion requires the consumer to display and recover
the actual backend result through its normal entry point.
