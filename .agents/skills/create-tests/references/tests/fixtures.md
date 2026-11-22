# Fixtures and factories

Read this before writing an arrange step, adding a builder, or changing a
production entity. It applies to `backend/tests/**` and
`backend/packages/printstash-core/tests/**`; the frontend equivalent is in
[frontend.md](frontend.md).

**The rule in one line:** a test's arrange step names the *state* it needs and
nothing else. Everything about how that state is encoded in the database lives in
`tests/factories/`.

## Use the `make_*` fixtures

`tests/integration/conftest.py` exposes every builder as a session-bound
fixture. Take the ones you need as parameters; never assemble a row inline.

```python
def test_hides_a_trashed_model_from_the_listing(
    client, auth_headers, make_model
):
    make_model("Bracket", trashed=True)

    response = client.get("/api/v1/models", headers=auth_headers)

    assert response.json()["items"] == []
```

Run `uv run pytest --fixtures -q tests/integration` for the current list, or read
`tests/factories/__init__.py`. Annotate a parameter with its protocol
(`make_model: MakeModel`) when the extra clarity earns its keystrokes — that is
what gives autocomplete and lets pyright catch a misspelled keyword.

Import a builder directly (`from tests.factories import build_model`) only where
a fixture cannot reach: inside another fixture, in a `conftest.py`, or in a test
that manages its own engine.

## Never re-encode state a keyword already names

Every builder keyword exists because getting the encoding wrong produces a row
that **inserts cleanly and is then invisible to the code under test**. That is a
silent false pass — the test goes green while asserting against a code path its
setup never reached. Several tests in this repo did exactly that for months.

| Write this | Not this | Because |
| --- | --- | --- |
| `make_model(trashed=True)` | `deleted_at=utcnow()` | every read filters through `scopes.live()` |
| `make_printer(provider=BAMBU_LAN)` | four `bambu_*` fields | one table holds all five providers' credentials, all nullable |
| `make_file(recommended=True)` | `is_recommended=True` | the builder demotes the previous holder; at most one per model |
| `make_external_library(scanning=True)` | a claim token | token *and* expiry *and* job id are checked together |
| `make_capture_slot(uploaded=True)` | `state=UPLOADED` | the import path also needs `storage_key` |
| `manifest_for_source(source)` | a hand-written manifest | provider, item id and URL must all match one source row |
| `make_share_link(expired=True)` | `expires_at=...` | easy to get the comparison backwards |

Passing the underlying column when a keyword owns it raises a `TypeError` naming
the keyword to use — that is `reject_aliases`, not a bug.

## One-off fields go at the call site

Every builder forwards `**overrides` straight to its model. A field only one test
cares about is set in that test, where the reader can see it:

```python
make_model("Bracket", source_url="https://example.test/thing")
```

Do **not** add a parameter to a builder for one caller. The builder is read far
more often than it is called.

Passing a provider field as `None` is how a test says "misconfigured on purpose",
and it reads as the deliberate omission it is:

```python
make_printer(provider=PrinterProvider.BAMBU_LAN, bambu_access_code=None)
```

## Authoring a builder

One builder per table, in the domain module that owns it (`identity`, `library`,
`printers`, `provenance`, `capture`, `ops`). Match the existing shape exactly:

```python
def build_thing(
    session: Session,
    positional_identity,          # what the row cannot exist without
    *,
    intent_keyword: bool = False, # a state, not a column
    **overrides: Any,
) -> Thing:
    """One sentence on what it is, then why any keyword exists."""
    reject_aliases(overrides, {"the_column": "intent_keyword"})
    overrides.setdefault("unique_column", unique_hash("thing"))
    return save(session, Thing(..., **overrides))
```

Rules:

- **Session first, positional, explicit.** The fixture binds it; nothing else.
- **Commit, don't flush.** `save()` does this. Production code under test
  frequently opens its own transaction, and a row in a pending flush is invisible
  to it — that mismatch caused several "works in isolation" bugs in the old
  helpers.
- **Generate every unique column** via `nth()` / `unique_hash()`. Never a
  hard-coded slug or sha: it works alone and collides on the second row. Never
  add a `made = {"n": 0}` closure — that is what `nth()` replaced.
- **Take related rows, not their ids.** `collection=collection`, not
  `collection_id=collection.id`. Derive what you can (a print job's `model_id`
  comes from its artifact).
- **Default to the state the app is usually in.** A printer defaults to `READY`
  because an offline one is skipped by dispatch, and a test that forgets it
  asserts against an empty fleet.
- **Add an intent keyword only for an encoding that can silently mislead.** If
  the column is self-explanatory, let `**overrides` carry it.
- **Add a `reject_aliases` entry** for every keyword whose name differs from its
  column. That is exactly where a caller guesses wrong.
- **Add the protocol** in `protocols.py` and the fixture in
  `tests/integration/conftest.py`, in the same commit.
- **Add rows to `tests/repo/test_factories.py`** for each promise the builder
  makes, asserting *through production predicates* — `scopes.live()`, the client
  factory, the validator — never by reading the column back. A builder that sets
  a column the app does not look at is the silent failure this system prevents.

Nothing in a builder may resemble a real credential. Printer access codes, API
keys and share tokens are obviously-fake placeholders.

## Scenarios: promote, never draft

A scenario is a *named multi-row state* in `tests/factories/scenarios.py`. The
bar for adding one is all three of:

1. **Three separate test files** already build the same shape, and
2. the shape has a name someone would say out loud ("a printed model", "a printer
   with a queue"), and
3. **every row in it is load-bearing for all three callers.**

Below three files, the assembly stays inline in the test that needs it. A
scenario with one caller is a helper with extra indirection; a scenario nobody can
name is a bag of rows whose contents the reader has to go and look up anyway.

Failing (3) is the common trap. If one caller needs a row the others do not, it is
**two scenarios** — merging them makes every test carry setup it does not use, and
readers cannot tell which rows matter to the assertion.

Each scenario's docstring says **why its shape is a unit** — what breaks if a row
is missing — because that is the thing a caller cannot see from the call site.

When a scenario drops below three callers, delete it and inline it back. A
scenario is a response to duplication, so it should disappear with the
duplication.

## Maintenance: entities and factories change together

**A production entity change is incomplete until its factory matches.** In the
same PR, not a follow-up:

| You changed | Also do |
| --- | --- |
| Added a table | Add its builder in the right domain module, its protocol, its `make_*` fixture, its `__all__` entries, and its rows in `test_factories.py` |
| Added a required column | Add a `setdefault` for it — otherwise every existing caller breaks with an `IntegrityError` that names the column but not the fix |
| Renamed a column | Update the builder; add a `reject_aliases` entry if a keyword now differs from the column |
| Added an invariant across rows | Enforce it in the builder (as `recommended=True` demotes) and add a `test_factories.py` row for it |
| Added a provider to `PrinterProvider` | Add its credential set to `_PROVIDER_FIELDS` — the parametrized builder test covers every enum member, so a missing entry fails loudly rather than producing a printer that cannot connect |
| Added an enum value a builder defaults to | Check the default is still the state the app is usually in |
| Deleted a table | Delete its builder, protocol, fixture and rows |

**A stale protocol is worse than none** — it type-checks a signature that no
longer exists. Keep it in step with its builder in the same commit.

**Never fork a builder.** If an existing one is close but not right, extend it
with a keyword or pass an override. Two builders for one table is how
`make_model` came to exist twice with incompatible argument orders, and how
`_user` came to exist thirteen times disagreeing about whether its default was a
superuser. When you find a divergent local helper, delete it and use the builder.

**Deleting local helpers is part of the work.** A test file should have no
module-level `_build_*` function that duplicates a factory. Single-use setup that
is genuinely local stays inline in its test, not in a helper at the top of the
file — a helper with one caller just moves the reader away from the assertion.

## Rows nothing may save

A few builders return a row and deliberately do not commit it, because the row's
*absence* from the database is the thing under test. `detached_model`,
`detached_file` and `detached_collection` feed the guards that must refuse an
id-less row — a purge that reasoned about one would delete bytes it has no record
of, so these branches are only reachable with a row that was never saved.

Separately, `printer_config`, `user_config` and `print_job_config` are the
*configuration* half of their builders without the persistence. The contract tier
has no session at all, and several pure functions take a row and return a
decision about it. `build_printer` is `printer_config` plus `save`, so the two
can never disagree about what a Bambu printer needs.

Reach for one of these only when a session would be inventing persistence the
test does not use. Everywhere else, the saving builder is the right tool.

## The migration ratchet

`tests/repo/test_test_hygiene.py` enforces the factory rules. One of them is now
absolute: **no test file builds a factory-owned row by hand.** Every file was
migrated, and the per-file exemption list is gone — if a file seems to need one,
the answer is a factory that covers its case (`printer_config` and the
`detached_*` helpers came from exactly that) or an entry in
`CONSTRUCTION_ALLOWED` with a reason.

One ratchet remains:

- `PENDING_DUPLICATE_BUILDERS` — builder names still defined in more than one file.

**It may only shrink.** A companion test fails if a listed entry has already been
cleaned up, so the count is the real remaining debt rather than a number somebody
forgot to update. A *new* duplicate name fails immediately.

If you touch a listed name for any reason, migrating it is usually the cheapest
part of the change: delete the local builder, call the factory, and make explicit
whatever state the local default was hiding. Then remove its line from the list in
the same commit.

## The frontend equivalent

`frontend/src/test-support/factories.ts` does the same job for API-shaped
objects, and exists for a *different* failure: a response literal written inline
that is missing a field. TypeScript stops helping the moment the object is spelled
out in a test, and `PrinterRead` has thirty-odd fields — so two files that each
write one drift apart, and a wire-type change has to be chased through every
literal by hand.

```ts
const printer = aPrinter({ status: "printing" });
const denied = aPrinter({ access: printerAccess({ can_print: false }) });
```

Rules specific to the frontend:

- **Complete objects, never an `as` cast.** A cast lets the object drift out of
  shape the moment the wire type gains a field. If TypeScript complains after a
  schema change, that complaint is the whole point.
- **Nested blocks are composed, not deep-merged.** `access` and `capabilities`
  have their own builders. A generic deep merge needs `unknown` parameters and a
  runtime `typeof` walk, both of which the anti-slop lint rules forbid — and
  composition reads better anyway, since the block being customised is named
  rather than inferred from nesting depth.
- **Absolute ISO timestamps** from the shared `FROZEN_NOW`, never `Date.now()`.
- **Defaults are the ordinary case** — a reachable, fully-capable printer — so an
  interesting variant is visible at the call site. A test asserting a control is
  hidden must turn its capability off, which is exactly the signal a reader wants.
- `_wire.ts` beside the API-client tests stays separate: that stands in for
  `fetch`, this builds the bodies it returns.

## Environment fixtures

Not every fixture is a row builder. `local_storage`, `backup_env`, `no_egress`
and the tier guards configure an environment. Keep them in the nearest
`conftest.py` rather than in `tests/factories/`, which is for database rows.

An environment fixture that mutates the config overlay must restore it on
teardown; `local_storage` shows the shape. A leaked overlay key is an
order-dependent failure in a completely unrelated test.

## Checklist

Before opening a PR that touches tests:

- [ ] No row assembled inline that a builder covers
- [ ] No new module-level helper duplicating a factory
- [ ] No hard-coded value in a unique column
- [ ] Every intent keyword used instead of its underlying column
- [ ] Any production entity change reflected in its builder, protocol and fixture
- [ ] Any new builder promise has a row in `tests/repo/test_factories.py`
- [ ] Any new scenario has three callers and a docstring saying why it is a unit
