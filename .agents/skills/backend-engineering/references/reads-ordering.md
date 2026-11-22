# Reads, projections, and ordering

## Start from the caller's contract

Describe the scope, visible row states, filters, order, bounds, and response
required by one coherent consumer. A list, detail view, export, summary,
and existence check answer different questions. Reuse an existing query
when its contract already matches. Use `modules/library/model_views` for
Library response composition and place other reads with their capabilities.

Resolve current scope in the read. Apply RBAC and shared live/trashed
predicates when fetching. For lookup by identity, make the uniqueness and
missing-target policy explicit. A first result is safe only where the
predicate proves uniqueness or the complete ordering defines the intended
selection. A database exception or malformed stored row remains unavailable.

## Push down and batch

Filter, join, aggregate, sort, and slice in SQL when the store can perform the
work. Select the fields the consumer needs. Use existing loaders or bounded
bulk lookups when composing a page. An ORM relationship can conceal query
fan-out. Avoid loading an unbounded table, counting per row, fetching extra
pages to filter unauthorized records, or asking the web client to assemble
the domain facts with per-item requests.

Count DB statements and bound parameters at realistic scale. Use the scale
harness routed by [test design](../../create-tests/references/testing.md).
A new collection-scoped Library read belongs in the existing scale inventory
when its cost can grow with library size. Index changes need both supported
database engines where the plan can differ.

## Make order part of the contract

- State the product order and intentional placement of missing values. Use
  SQL expressions or the established query helper so SQLite and PostgreSQL
  agree when the behavior matters.
- End an ordered sequence with the selected value's own stable unique key
  after all product sort keys. A related object's ID may still tie. Apply the
  same order to in-memory merges over candidate values.
- Encode page cursors through the existing owner. Cursor predicates and
  cursor encoding must use the same keys, null handling, and directions as
  the actual order. Preserve scope, filter, and revision binding. The
  existing Library browse cursor describes a position in a revision, so a
  changed revision follows the established recovery path.
- Keep unordered membership and keyed lookups in set or mapping form
  internally. Convert to a sequence at a boundary that defines its order.
- Test with values tied on every product key and seed in a different order
  from the expected unique-key order. Include absent values and a tie that
  crosses the page boundary when applicable. Verify both that no row
  disappears and that no row repeats.

## Name dependencies and freshness

Keep stable read identity separate from mutable results. State which
persisted facts, authorization changes, settings, clock readings, or
external inputs can change the result. Pass bounded inputs explicitly. Use
the existing projection revisions, event channel, or cache invalidation to
account for every influence. Keep large bytes out of maintained result
snapshots. See
[frontend-backend integration](../../frontend-backend-integration/SKILL.md)
for the client cache, paging, and cancellation contract.
