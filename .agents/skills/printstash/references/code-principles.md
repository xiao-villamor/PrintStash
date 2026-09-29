# Code principles: make invalid states unrepresentable

Binding for every backend and frontend change. A reviewer should be able to read
a type and know every value it can hold; the code should never have to guess.

## Model the domain in types, not in strings

- **A closed set of values is an enum, never a free string.** Job kinds, lanes,
  states, roles, derivative kinds, error codes a client switches over: a Python
  `str, Enum` on the backend, a string-literal union on the frontend. A value
  outside the set must fail to type-check or fail loudly at the boundary, not
  flow on as `str`.
- **The database enforces the same set.** An enum column is TEXT plus a named
  CHECK constraint, through `app.db.enum_columns` (`EnumText` + `enum_check`).
  Never a database-native enum type. See
  [database.md](database.md#enum-columns-text-and-a-check-constraint).
- **Make exclusive cases distinct types.** Two things that share a few fields
  but mean different things are two types (or a tagged union), not one type
  with fields that are "only set when…". For example: `JobSubmission` versus
  `PassSubmission`, `Deduplicated` versus `Partitioned` routing. If a field is
  meaningful only in some states, either the type is split or a CHECK
  constraint and a validator tie the field to those states.
- **Required means required.** A field that is always present is not
  `Optional` and not `?:`; one that is present only sometimes says when in its
  type or its constraint. Mirror the backend schema exactly in hand-written
  frontend types: a backend field that is always sent is a required property.
- **Structured values are structured.** A fence scope, a subject, a list of
  lanes: columns or a small type, not a string with a `prefix:` and a
  `split()` to read it back.

## Treat invalid situations as errors

- **No magic fallbacks.** No `x or ""`, `x or {}`, `value ?? "unknown"`,
  `.get(key, default)` where a missing key is a bug, `int(x) or 1`, `str(exc) or
  "some_code"`, `[:64]` truncation to make a value fit. If the value can
  legitimately be absent, the type says so and the caller handles it; if it
  cannot, absence raises.
- **No sentinel values.** `0` for "not configured", `""` for "no id", `-1` for
  "none", `"{}"` for "no manifest". Use `None` in an `Optional` that the type
  and every reader handle explicitly, or a dedicated enum member.
- **Unknown input from outside is rejected at the boundary**, with a typed
  error (`OperationError` with an `ErrorKind`), not coerced to something
  plausible. Unknown data from our own storage (an engine status, a stored
  enum value) is a bug: raise, do not map it to "absent".
- **Catch narrowly.** `except Exception` only where the documented contract is
  "never raise into the caller" (a nudge, a best-effort notice), and then log
  it. Everywhere else catch the specific exception you can handle.
- **Validate once, then trust the type.** Parse at the edge (request schema,
  settings, row load) into the precise type; interior code does not re-check
  or defend against values the type already excludes.

## Scale with the library, not with the rows a test has

PrintStash supports 25,000 collections and 100,000 Models
(`docs/known-limitations.md`). A test library holds a dozen rows, where
quadratic work and per-row queries are instant; #295 was a sidebar that took a
minute at 9,000 collections and passed every test. So:

- **No loop over the whole set inside a loop over the whole set.** Comparing
  every collection with every other is the classic shape. Roll a tree up in one
  pass (`taxonomy.subtree_totals`), group in SQL, or look up in a dict.
- **Never bind a materialised id collection into a query.** A visible-set
  filter is a subquery (`rbac.accessible_collection_ids_stmt`), not
  `.in_(accessible_collection_ids(...))`: that binds one parameter per visible
  row into every statement. A set is for membership checks in Python.
- **A list endpoint is bounded.** It takes `limit: int = Query(..., le=...)`, or
  it is registered in `tests/repo/test_list_endpoints_bounded.py` with what
  keeps it small. Returning a whole table is debt, and that list only shrinks.
- **A read runs a fixed number of statements.** Batch per page, never per row.
- **Long UI lists do not render every row.** Cap what a first render opens, and
  never walk a subtree inside a row's render; compute it once per tree.

A collection-scoped listing is registered in `backend/tests/_library_reads.py`,
which checks its shape on every PR and its time at the supported size nightly.

## Review checklist

Before calling a change done, grep your own diff for: `or ""`, `or {}`, `or []`,
`?? `, `|| ""`, `.get(` with a default, `= ""` field defaults, `Optional[` and
`?:` on fields the producer always sets, bare `except Exception`, slicing to a
length (`[:N]`), `str` parameters that take one of a known set of values, and
TypeScript `string` where a union exists. For reads, also: nested loops over the
same set, `.in_(` fed by a Python collection, and a new GET returning a list. Each hit either gets a type, becomes
an error, or carries a comment saying why absence is a real, handled state.
