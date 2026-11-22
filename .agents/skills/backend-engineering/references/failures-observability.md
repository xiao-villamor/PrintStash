# Failures and observability

## Preserve semantic outcomes

A required target that is absent refuses through the owning error. An
optional lookup can return a confirmed absence. An empty result follows a
successful read. Conflict, unknown outcome, and unavailable dependency
represent distinct recovery choices. Keep a legitimate degraded result
explicit and retain its cause through the owning diagnostics channel.

Follow `app/core/errors.py` and `app/api/errors.py` for the existing
`OperationError`, `ErrorKind`, and HTTP mapping. A capability exposes a
stable, bounded code and permitted metadata. Translate foreign failures at
the adapter that understands them and preserve the original cause internally.
Catch a specific case in another capability when it needs to add meaning.
Let unrelated failures propagate to their own boundaries.

Provider diagnostics, credentials, submitted file data, and arbitrary
exception text stay out of public error fields. Keep authentication failure,
provider access failure, a stale edit, capacity, and transient unavailability
distinct where the caller's next action differs. The frontend maps known
codes to localized recovery through the existing error path.

## Reconcile without losing evidence

On incomplete, unreadable, or ambiguous authoritative inventories, preserve
local state and surface the failed read. Zero results from a successful scan
can support a decision. A partial result cannot establish absence. A
reconciler may record a bounded item failure and continue only along
independent branches. Corrupt identity or ownership evidence blocks mutation
until the owner can establish which fact is authoritative.

Cancellation is a lifecycle outcome. Keep user cancel, disconnect, timeout,
provider refusal, and an unknown response separate. If an effect might already
have happened, read the owning state or receipt before replaying it.

## Choose the channel by its consumer

| Channel | Use |
| --- | --- |
| Durable state and Job outcomes | Commit authoritative facts that a caller can recover after reconnect or restart. |
| Domain and Job events | Publish structured signals through the existing sink after the fact they describe. Consumers recover truth from the owning read. |
| Audit records | Record authorized changes through the owning audit code and its redaction rules. |
| Logs | Explain the concrete cause to an operator with a stable message and safe structured fields. |
| Metrics | Track distribution, count, latency, and work health with bounded label vocabularies through `app/core/metrics.py`. |

Use the existing request or execution context to correlate diagnostics. Keep
unbounded IDs in logs where safe, out of metric labels. Avoid usernames,
tokens, paths with sensitive content, signed URLs, and raw provider bodies.
Avoid double logging the same handled error across every layer. Best-effort
instrumentation failure should leave the underlying product outcome intact.

Assert observable state transitions, typed outcomes, and event contracts.
Only test log or metric details when the instrumentation itself is the
contract. Check split worker topology before assuming a metric emitted by a
worker is observable in the API process.
