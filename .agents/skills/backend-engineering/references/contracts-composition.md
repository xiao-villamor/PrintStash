# Backend contracts and composition

## Method, value, policy, or capability

- Give a domain value the operations it can answer with its own data. Make
  construction validate intrinsic constraints. A parser converts foreign
  input into this value or refuses it at ingress.
- Use a plain policy function for decisions over already resolved facts.
  Choose the facts and their authoritative sources in the capability.
  A limit, state transition, or normalization rule reused by both create
  and update belongs in the same owner.
- Give the capability an operation when it must load targets, establish
  authority, decide next steps, or stage a durable effect. Expose that
  operation to other capabilities. Avoid exporting its internals so callers
  can rebuild the same rule.
- Give read projections their own meaning. A command can return a receipt
  without dragging in every UI projection. A pure answer about a feature
  still comes from the feature even when no DB query is needed.
- Plain DTOs, persistence rows, and codecs serve different consumers.
  Preserve the names of semantic fields when mapping. Keep structural
  conversion separate from authority checks and product policy.

## Admit a port when it protects something

A port represents a real boundary, such as replaceable storage, transport,
execution, or persistence. Describe the operation the capability needs,
including errors and lifecycle. One app-wide repository or service container
typically makes the available power much broader than that operation.
Fixture injection alone does not justify another adapter. Use narrower
parameters or the existing test seam when they suffice.

`printstash-core` can own pure computation or protocol behavior that has a
portable consumer. Keep external SDK details on the adapter side and keep
optional implementations optional. Apply this rule to signatures, defaults,
and imports too. An eager import of an optional feature creates a startup
dependency even when callers never use it.

## Compose once at the lifetime owner

Find the existing recipe in `app/bootstrap/` before constructing a process
resource. Add the resource at the narrowest scope that owns startup and
teardown. Pass only the dependency needed to a capability. A request borrows
a session through the existing dependency. A background step obtains its
own resources from Job composition. Neither retains a session obtained in
another request or executor.

Keep resource ownership directional. Initialise in dependency order, clean
up partially acquired resources on startup failure, and stop admitting work
before closing the resources it needs. Use explicit, idempotent cleanup for
resources that may be closed on both failure and shutdown. Aggregate failures
when independent cleanup must still proceed.

Executors own where work runs. Pay attention to blocking filesystem, database,
parser, and provider work even within an async function. Reuse the existing
worker, compute, or storage entry point for that payload. Crossing an
executor carries values and narrow capabilities, with authority and resource
lifetime checked again at the receiving owner.

## Check the existing boundaries

- `app/bootstrap/dependencies.py` and `app/bootstrap/lifecycle.py` own HTTP
  construction and process wiring.
- `app/bootstrap/work.py` and `app/modules/work/` own Job composition and work
  contracts. The engine adapter handles execution mechanics.
- `tests/repo/test_architecture.py` and
  `tests/repo/test_forbidden_imports.py` govern dependency direction.
- Use [identity and writes](identity-writes.md) for the operation's authority
  and persistence lifetime, and [reads and ordering](reads-ordering.md) for
  consumer projections.
