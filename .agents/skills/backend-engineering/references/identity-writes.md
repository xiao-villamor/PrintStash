# Identity, persistence, and writes

## Separate identity, scope, content, and representation

A stable entity reference selects a target. Scope and current permissions
grant authority over it. An edit base establishes which version the caller
saw. A storage key addresses bytes and needs ownership evidence of its own.
Carry each explicitly when it matters. A Model ID, File/Artifact reference,
Job ID, or provider key cannot substitute for another just because they share
a scalar representation.

Keep existing identifier formats. Use the typed DTO or domain wrapper where
one exists. Build identity from semantic inputs at its creation owner. Map
persisted columns in the storage adapter and reconstruct explicit absence
without default values that change its meaning. Refuse contradictory evidence
before applying a mutation. Read the [database](database.md) and
[storage data safety](../../../../docs/storage-data-safety.md) rules before
changing keys, relations, scope, or deletion behavior.

Keep stable identity and server-owned metadata out of editable drafts.
A change that creates a new incarnation, Revision, or relationship needs
its own operation and compatibility story. Update references, indexes,
wire contracts, and migrations together when a real identity recipe changes.
Retain soft-deleted state and storage ownership until the designated lifecycle
authorizes removal.

## Ownership of the transaction

1. **Resolve the intent.** Classify the write as an element edit, a single
   multi-record action, or a documented best-effort batch. Use the aggregate
   operation for an aggregate user action. Element editing should update
   just the affected element and preserve sibling IDs.
2. **Validate within the right boundary.** Resolve required rows under
   current authority and visibility. For an atomic action, validate the
   complete target set before staging. Where another writer can race, use
   the owning conditional write, constraint, or lock. Follow
   `backend/app/db/transactions.py` for transaction mechanics and
   `modules/library/edit_preconditions.py` for conditional Library edits.
3. **Stage and commit once.** The operation that defines atomicity owns SQL
   commit and rollback. Helpers stage rows and can flush when necessary.
   Follow existing commit ownership when reusing them from a batch. Keep
   reads free from staged side effects. A flush may make a generated key
   available while the transaction can still roll back.
4. **Return a confirmed result.** Expose the committed status, changed
   authority or version, and relevant IDs. A no-op should preserve version
   metadata when the contract promises it. An undo operation must capture
   the complete prior value and actual inverse preconditions inside the
   same atomic boundary, including absence where it matters.

Avoid holding a transaction across network, hardware, user interaction, or
slow filesystem work. A database rollback reverts SQL. Local file moves,
objects, printer actions, and external requests need explicit handling in
their owning contracts.

## Work across commits

Use the [Job and Work Source](background-work.md) model for recoverable work.
Record the intent with the state that establishes it, then commit before the
engine nudge or read invalidation. The nudge is timely delivery of a hint.
A worker re-derives the needed work from the authoritative state.

Each phase declares its durable precondition, side effects, and completion
evidence. Record generation, version, or a fence where a late completion
could overwrite a newer target. After I/O, open the right scope again and
revalidate before recording success or releasing ownership. Redelivery and
restart should converge on the same intended result. When the reply is lost,
reconcile with recorded state before allowing a blind repeat.

Maintain honest terminal states. A requested Job, completed SQL transaction,
external acceptance, and durable completion can occur at different times.
Cancellation withdraws the appropriate intent so the reconciler does not
immediately re-create it. An expired or failed attempt retains the evidence
needed to tell what actually happened.

For storage publication or deletion, follow the capability's ownership,
receipt, and cleanup protocols. An incomplete inventory cannot authorize a
destructive action. Keep failure durable and retryable where the contract
permits it.

## Verify failure and recovery

Exercise rollback after partial staging, concurrent or stale changes,
unauthorized targets, retry after an ambiguous response, and recovery after
a crash or state change where supported. Check the persisted rows, bytes,
Jobs, and receipts that demonstrate each invariant. Use the relevant
[coverage policy](../../create-tests/SKILL.md).
