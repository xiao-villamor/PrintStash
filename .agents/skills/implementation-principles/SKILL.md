---
name: implementation-principles
description: Apply PrintStash implementation principles when planning, placing, or reviewing code, including ownership, module boundaries, identity, lifetime, errors, and scale.
---

# Implementation principles

Read [codebase-design](../codebase-design/SKILL.md) for depth, leverage,
locality, and seam design. Use [CONTEXT.md](../../../CONTEXT.md) to name
domain concepts and [codebase-architecture](../codebase-architecture/SKILL.md)
to find their owners. The accepted product contract governs intended behavior.
Read current code to understand implementation and established seams.

## One concept, owner, and home

Give each invariant one authoritative implementation and place it with the
owner that can enforce it for every caller. Make dependencies explicit and
narrow. Keep product decisions with capabilities, UI decisions with
presentation owners, and execution mechanisms with runtime. A feature's use
of another capability goes through that capability's public contract.

Fix the owning shape when repeated special cases, flags, or fallback paths
point to a misplaced responsibility. Migrate the affected callers together.
Preserve public API, IDs, and support for existing installs as required by the
contract. A compatibility adapter with an identified caller and lifecycle can
be necessary. A duplicate private implementation loses its reason to exist
once all consumers use the owning path.

## Code organization

Apply this assessment before placing code and again during Standards review.

1. **Establish cohesion.** Inspect the complete affected owner, its siblings,
   callers, and dependencies. Name its rules, execution lifetime, and
   authority. Find the pieces that change together. A shared field shape,
   suffix, or database ID gives little evidence of shared ownership.
2. **Choose the organization.** Keep a cohesive owner flat. Extract a semantic
   group when a part has independent behavior a maintainer can understand on
   its own. Move a rule when another owner already has the required context.
   Combine owners that split one lifecycle without protecting distinct
   authority. Name each disposition and its reason.
3. **Choose the boundary separately.** A file groups a declaration and its
   private details. A folder groups a meaningful family. An interface protects
   a real dependency, authority, lifetime, or substitution boundary. A package
   needs a concrete dependency or distribution purpose that costs less than
   its public API and maintenance. Check repository dependency enforcement
   before changing the boundary.
4. **Check for both forced fit and fragmentation.** Forced fit puts feature
   policy in generic machinery or unrelated lifecycles behind one service.
   Fragmentation spreads one semantic step across forwarding methods and
   folders that mirror each other. Inspect behavior, caller burden, and
   dependency direction. Size and nesting depth are prompts to investigate.
5. **Move the consumers.** Update callers, wiring, imports, schemas, test
   fixtures, documentation, and path-sensitive tooling. Remove obsolete
   private boundaries. Verify old persisted data and public callers still use
   the supported path.

Before review, zoom out over the full affected owners and the accumulated
diff. Record what stayed, moved, or combined and why in the existing plan or
review. A standalone architecture audit inventories its requested scope.
Ordinary implementation checks the owners that the task affects.

## Identity, authority, and lifetime

- Distinguish a stable product reference, mutable content, authority to act,
  and physical storage location. For example, a Model ID locates the Model.
  The current user's scope authorizes the operation. An Artifact's storage
  key locates bytes and requires ownership evidence.
- Preserve the declared reference family across reads, relationships,
  navigation, and writes. An ID is never a grant of access. Resolve current
  truth in the receiving owner under current authority. Keep snapshots when
  the contract needs historical evidence or a specific edit base.
- Give closed states explicit types and represent absence only when it has
  meaning. Separate product truth from transient execution and interaction
  state. An admitted Job owns durable completion even if its initiating page
  closes. Keep disposable interaction state with the narrowest stable owner.
- State who can start, cancel, retry, and complete the operation. Treat
  executor isolation, database atomicity, and authorization as independent
  guarantees. An await, transaction boundary, or worker handoff can advance
  state. Revalidate version, authority, and target before the durable apply.
- Keep wiring acyclic. A capability gets a narrow dependency and lifecycle
  owner. Break a cycle by moving the rule or inverting the dependency.
- Apply irreversible effects after durable prerequisites succeed. If an
  operation offers undo, capture the actual inverse and its preconditions
  with the write. A visible prior value can miss an unset override, old
  version, or resource ownership.

## Preserve truth at boundaries

Choose what the operation promises. A read can return a confirmed empty
collection or optional absence. A guard refuses when it cannot establish the
required state. A failed read is unavailable. A mutation that already achieved
its goal may be idempotent if it can prove the same intent and durable result.

Carry bounded semantic failures across the owning boundary. Translate
foreign failures once where the adapter understands them. Keep diagnostics
redacted and separate from response codes and user-visible wording. Follow
[backend failures and observability](../backend-engineering/references/failures-observability.md)
for their concrete PrintStash paths. For reconciliation, preserve local state
when inventory is incomplete. Isolate an item failure only when sibling work
is provably independent.

Keep each write rule with its domain owner. Validate on every authoritative
write path. Reflect the same constraint through the UI's offered controls or
server-provided capabilities. Cross-record and concurrent invariants need a
database constraint, conditional write, lock, or another actual serialization
mechanism. A preliminary validation query alone does not establish the invariant.

### Typed data for each purpose

- Parse the external representation at its adapter, then expose structured
  facts. Pass identities and version tokens unchanged through consumers.
  Derive a name, sort value, badge, or status from its own field. A storage
  key, formatted label, or serialized enum is a poor substitute for a typed
  contract.
- Keep one authoritative fact. Produce purpose-named projections from it
  rather than storing a second editable copy. A denormalized index or
  projection needs explicit rebuild and refresh ownership.
- Use a set for membership, a mapping for keyed identity, and an ordered
  sequence for promised order. Apply deterministic ties for pagination and
  selection. See [backend reads and ordering](../backend-engineering/references/reads-ordering.md).
- Keep calculation in the domain and format numbers, times, and labels at
  presentation. When a consumer needs a missing fact, add the fact to the
  owning projection instead of splitting a string or decoding an ID.

## Prefer concrete designs

Build for actual consumers. Name a value or give it a method when it owns a
real invariant or operation. Use a plain function for a rule over resolved
values and a narrow parameter for a dependency. Use a factory when it owns
construction policy. Keep ordinary validated wrapping or value construction
at the existing constructor. Avoid an interface or registry that adds only
a second name for the same step.

Bind substantive steps to semantic names. Name the validated request, selected
release, computed storage plan, or confirmed receipt before using it. Keep
literals, simple predicates, and cohesive UI composition inline. A mutable
binding belongs where mutation occurs, with evaluation order and side effects
intact. Direct control flow makes sequencing easier to check than nested
conditionals, callbacks, or aliases named for evaluation order.

Explain an invariant or ordering constraint where the owner enforces it.
Record a durable trade-off where maintainers can recover the alternatives and
why the choice fits. Follow [settle-printstash](../settle-printstash/SKILL.md)
when the result changes a maintained contract.

## Completion

Use [code principles](references/code-principles.md) for type, fallback, and
scale checks. Use [create-tests](../create-tests/SKILL.md) to prove contracts,
transitions, isolation, and failure shapes at the real entry points. Finish
when each invariant has one owner, each boundary expresses its actual
authority and lifetime, and each changed consumer has verified evidence.
