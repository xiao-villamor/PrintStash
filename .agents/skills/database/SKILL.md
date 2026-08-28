# Database

Invoke before any schema change, migration, or query that touches soft-deleted rows.
SQLite is the default installation and PostgreSQL is optional, so every rule here
has to hold on both.

## The regular workflow: changing the schema

The short answer to "do I hand-edit anything?" — **you edit the generated file, you
never author the DDL.** In a converged repo that edit is small, because autogenerate
emits only your change: verified, and held by
`test_models_versus_chain.py::TestAutogenerateIsEmpty`, which fails if
`--autogenerate` against the chain would emit anything at all.

Adding a nullable column, start to finish:

```bash
# 1. Change the model. This is the source of truth — `create_all` builds new
#    installations straight from it.
$EDITOR app/db/models.py

# 2. Generate. Emits `with op.batch_alter_table(...)` for SQLite, and renders
#    sqlmodel's types as plain SQLAlchemy ones.
cd backend && uv run alembic revision --autogenerate -m "add model.notes"

# 3. Read it — see "How to read a generated migration" below. Should be one
#    `add_column`. Replace the generated docstring with what the change is for.
$EDITOR alembic/versions/<rev>_add_model_notes.py

# 4. Round-trip it.
uv run alembic upgrade head && uv run alembic downgrade -1 && uv run alembic upgrade head

# 5. Test the behaviour the column exists for, and the migration itself if it
#    rebuilds a table or touches rows.
./scripts/test.sh coverage
```

What each step is actually protecting:

| Step | Without it |
| --- | --- |
| 1 before 2 | autogenerate has nothing to compare against and emits an empty migration |
| 3 | the 890-line convergence migration ships as "add a column" — or a rename ships as a `remove_column`, which is silent data loss |
| 3, docstring | the next person debugging an upgrade has your `-m` message and nothing else |
| 4 downgrade | a rebuild that loses rows on the way back is found by a self-hoster |
| 5 | see rule 4 below |

**Adding a constraint** — a foreign key, a unique constraint, a column type change —
is the same five steps plus two, because SQLite rebuilds the table:

```
3a. Repair first. Autogenerate compares schemas and knows nothing about rows, so
    a constraint added over data that already violates it is hand-written work.
3b. Verify after, scoped to what you added.
```

Both are spelled out under **Repair before you constrain**. The rebuild itself is
fast — 10 ms per 10,000 rows — and needs foreign keys off, which Alembic's engine
already leaves off.

**Changing a column's nullability or type** is a rebuild too, so it goes down the
constraint path, not the column path.

## How to read a generated migration

Step 3 above says "read it", which is only useful if you know what you are reading for.
Autogenerate is a schema differ. It is very good at that and blind to everything else,
and the blind spots have a shape worth memorising.

### It cannot see a rename. It sees a deletion

```python
# You renamed `things.old_name` to `new_name`. Autogenerate emits:
('add_column',    'things', Column('new_name', String(64)))
('remove_column', 'things', Column('old_name', VARCHAR(64)))
```

Ship that and every value in the column is gone.

**No setting fixes this, and it is not an Alembic defect.** Autogenerate compares two
*states*: the database's schema and the models' metadata. Renaming `old_name` to
`new_name`, and dropping `old_name` while adding an unrelated `new_name`, leave the
database in the same place — so the differ produces byte-identical output for both.
`tests/integration/db/migrations/test_autogenerate_guards.py` asserts that equality,
because it is the reason the rest of this section exists. The distinguishing fact is
what you meant, and that was never written anywhere a differ can read it. Django
resolves the same ambiguity by *asking*, interactively; Alembic is non-interactive by
design.

**So `env.py` refuses to write the file instead.** `process_revision_directives` stops
the run when a migration would drop and add columns in the same table:

```
this migration would drop tags.name and add tags.label — which is what a *rename*
looks like to a schema differ, and shipping it as a drop destroys the column's data.
  If it is a rename: replace both operations with
  `batch_op.alter_column("name", new_column_name="label")`.
  If it really is a drop: re-run with `-x allow_column_drop=tags.name`.
```

That is the Django prompt in non-interactive form: the intent has to be stated, once,
by the person who has it.

#### When the guard fires, you are at a fork

Nothing was written, so there is no file to fix yet. Which branch you are on decides the
next command.

**It is a rename.** Do not use the escape hatch — a rename is not a drop, and forcing one
out of autogenerate would only give you two operations to delete. Write the one operation
instead:

```bash
uv run alembic revision -m "rename tags.name to label"     # note: no --autogenerate
```

```python
def upgrade() -> None:
    op.alter_column("tags", "name", new_column_name="label")

def downgrade() -> None:
    op.alter_column("tags", "label", new_column_name="name")
```

Outside a batch block, so SQLite runs `ALTER TABLE … RENAME COLUMN` in place rather than
rebuilding — see the table above. Then re-run `--autogenerate` and confirm it emits
nothing: that is the proof the models and the chain agree again.

**It really is a drop, plus an unrelated add.** Acknowledge it by name:

```bash
uv run alembic revision --autogenerate -m "drop tags.name, add tags.slug" \
  -x allow_column_drop=tags.name
```

The acknowledgement is not just a flag to get past the check. `env.py` writes it into the
migration's message, so it lands in the docstring, the filename and `alembic history`:

```
"""drop tags.name, add tags.slug [confirmed data-dropping, not a rename: tags.name]
```

That is the point of the escape hatch being noisy. An acknowledgement that lived only in
the shell history of whoever typed it would leave a reviewer looking at `add_column` next
to `remove_column` with no way to tell a vetted drop from the rename this guard exists to
catch — the same failure, one step later.

Several columns are comma-separated: `-x allow_column_drop=tags.name,models.old_slug`.

**You are not sure.** Then the answer is not a migration yet. `git stash` the model
change, look at what reads the column, and come back. A drop is irreversible on a
self-hoster's data and the downgrade cannot bring it back.

#### How to actually write the rename

Alembic's own documentation lists column and table renames under *what autogenerate
does not detect*, and says its output "is always to be reviewed and corrected by hand".
So this is the sanctioned path, not a workaround: you correct the generated file.

Measured, because the cheap form is not the obvious one:

| How you write it | What SQLite runs |
| --- | --- |
| `op.alter_column("things", "old_name", new_column_name="new_name")` | `ALTER TABLE things RENAME COLUMN old_name TO new_name` — **in place**, no rebuild |
| the same inside `with op.batch_alter_table(...)` | full table rebuild |

Both preserve the data. But autogenerate wraps everything in batch blocks — that is
what `render_as_batch` does — so the correction is to **replace the `add_column` and
`remove_column` with an `alter_column` outside the batch block**, not inside it. SQLite
has had `RENAME COLUMN` since 3.25; batch mode is for the things it genuinely cannot do,
and a rename is not one of them.

```python
def upgrade() -> None:
    # Autogenerate offered a batch block with add_column + remove_column. This is the
    # same change without the data loss, and without the rebuild.
    op.alter_column("things", "old_name", new_column_name="new_name")
```

This is the one place rule 1 below bends, and it bends because Alembic says so: *never
author DDL from scratch* is about not inventing operations the tool can generate
correctly. A rename is a documented blind spot — the tool cannot generate it, tells you
so, and expects the correction.

The same reasoning applies to a table rename and to splitting one column into two —
neither is visible to a state comparison either, and neither is guarded by the hook, so
they are still yours to notice.

The same hook also **declines to write an empty migration**. `--autogenerate` with
nothing to do otherwise produces a file whose `upgrade()` is `pass`, and a chain
collecting those makes every later `alembic history` harder to read for no benefit.

### What each operation means, and the trap in it

| You see | It means | Check |
| --- | --- | --- |
| `add_column` + `remove_column`, same table | a differ that cannot see renames | is it a rename? see above |
| `alter_column(..., server_default=None)` | **dropping** an existing server default | the model has a Python-side default instead — fine through the ORM, and a raw `INSERT` omitting the column will now fail |
| `alter_column(..., type_=sa.Enum(...))` | a `VARCHAR` becoming an enum, which on SQLite is a `VARCHAR` + `CHECK` | every stored value must satisfy the new constraint, or the rebuild fails. Repair first |
| `alter_column(..., nullable=False)` | a NOT NULL over existing rows | any row holding NULL fails the rebuild. Backfill first |
| `drop_index` + `create_index`, same name | a *definition* change, usually the `unique` flag | not churn — read both lines |
| `drop_constraint` + `create_foreign_key`, same name | an `ondelete` or target change | which direction? `vault_audit_findings.run_id` was a real one: the chain had `CASCADE`, the models did not, and the models were the ones lagging |
| any constraint or type change on SQLite | a table rebuild | needs foreign keys off, and the rebuild is what the repair/verify steps exist for |

### What it never emits, so you always add it

- **Anything about rows.** Repairs, backfills, and the verification that they worked.
  A differ compares schemas.
- **A docstring worth reading.** It writes your `-m` message. Replace it with what the
  migration is for and what it assumes about the data it will meet.
- **Triggers, views, and anything outside `target_metadata`.** Not compared, so not
  emitted, so not dropped either — which cuts both ways.

### The check that catches the rest

Count the operations against the model change you made. One nullable column should be
one `add_column`. If the file is bigger than the change, something drifted before you
got here — read the extra operations rather than deleting them, because they are
telling you the schemas were already apart. That is exactly how the 890-line
convergence migration was found, and the parity tests now fail before it can happen
again.

## Migrations

### Never hand-write one

Generate it:

```bash
cd backend && uv run alembic revision --autogenerate -m "add durable capture slots"
```

Then **read the result before keeping it**. Autogenerate produces a draft, and two
things are routinely wrong with it:

- **It offers everything it noticed, not what you meant.** The first run of the
  convergence work emitted 890 lines and 242 operations because the two schemas had
  drifted. When that *is* the change, keep it all; when you meant to add one
  constraint, delete the rest. Either way it is a decision, not a formality.
- **It can emit code that does not run.** It reaches for the type object on the model,
  which for a `str` field is `sqlmodel.sql.sqltypes.AutoString`, and does not import
  it — the script dies at `NameError: name 'sqlmodel' is not defined`. `env.py` now
  passes a `render_item` hook that renders those as `sa.String`, which is what they
  are, and keeps a historical record free of the ORM layer's internals.
  `tests/integration/db/migrations/test_schema_convergence_migration.py` fails if one
  slips through.

Hand-writing the whole thing is how this repo ended up with two different schemas at
the same `head`.

`alembic/env.py` passes `render_as_batch=True` for SQLite, which makes autogenerate
wrap operations in `with op.batch_alter_table(...)`. That is a **rendering** flag: it
shapes what `alembic revision --autogenerate` writes into the file and does nothing
for a migration you type yourself. A hand-written `op.create_foreign_key` is a plain
`ALTER TABLE … ADD CONSTRAINT` whatever `env.py` says — and SQLite has no such
statement, so it fails with `near "FOREIGN": syntax error`.

That is exactly what happened in `69b6a6d8a1d1`, and the author resolved it with
`if not is_sqlite:` around the constraint. The column landed on every dialect and the
constraint on none of the SQLite ones. Eighteen foreign keys the models declare do
not exist on any installation that upgraded through the chain, and nobody noticed for
three months. `tests/repo/test_migration_patterns.py` now fails on that shape.

### The four rules

1. **Autogenerate the DDL; edit the file freely.** These are not in tension, because
   they are about different things. (One documented exception, from Alembic itself: a
   *rename* cannot be generated, so it is written by hand — see "It cannot see a
   rename" above.)

   *Never author a DDL operation from scratch* — `op.create_foreign_key`,
   `op.alter_column`, `op.create_index`. Autogenerate knows the dialect's limits and
   this repo's `render_as_batch` setting; a human typing the same call does not, which
   is the entire history in the section above.

   *Always edit what it generated*, before committing it:

   - **Delete what is not your change.** Autogenerate offers every difference it
     noticed. On a synchronised repo that *is* your change and there is nothing to
     delete — verified: autogenerate against the current chain emits `pass`, and
     `test_models_versus_chain.py::TestAutogenerateIsEmpty` keeps it that way. The 890
     lines and 212 operations that had to be trimmed once were three months of
     accumulated drift, not a standing review burden. If a generated migration is much
     bigger than the change you made, that is the signal something drifted — read it,
     do not trim it blindly.
   - **Write the docstring.** The generated one is the `-m` message. Say what the
     migration is for and what it assumes; a migration is read years later by someone
     debugging an upgrade.
   - **Add the data work.** Autogenerate compares schemas and knows nothing about
     rows. Repair steps, backfills, and the verification below are always hand-added.

2. **Never touch a migration that has been merged.** Self-hosters have run it; editing
   it changes nothing for them and desynchronises everyone else. Fix it forward with a
   new revision. (AGENTS.md hard rule 1.) This is why the 78 reflection-based
   `batch_alter_table` calls in the chain stay as they are.

3. **Never guard a constraint operation on the dialect.** `if not is_sqlite:` around
   `op.create_foreign_key` produces a schema that differs by installation, silently —
   it cost this repo 136 structural differences and four production bugs. Branching per
   dialect is fine and sometimes necessary; *skipping the operation* is not. Use
   `op.batch_alter_table`. `tests/repo/test_migration_patterns.py` enforces it.

4. **Every migration ships with a test** that runs it. Seven files for 67 migrations, so
   this is a rule for new work rather than a description of the chain — the ones that
   have tests are the ones that repaired data or rebuilt a table, which is the right
   priority. A migration that only adds a nullable column is covered by the parity
   tests below.

### SQLite cannot ALTER a constraint. Batch mode rebuilds the table

`ALTER TABLE` on SQLite supports four things: rename table, rename column, add
column, drop column. Everything else — adding or dropping a foreign key, a unique
constraint, a check constraint, changing a column type — needs the table rebuilt.

`op.batch_alter_table` does that: create `_alembic_tmp_<table>` with the full target
definition, `INSERT … SELECT` the rows across, drop the original, rename. Alembic
picks per batch (`recreate="auto"`, the default), and the line falls in a place worth
knowing exactly, because **a new column that is a foreign key is on the expensive
side**:

| What the migration does | What runs |
| --- | --- |
| `add_column`, no constraint | `ALTER TABLE … ADD COLUMN`. No rebuild. |
| `op.add_column` with an inline `ForeignKey`, no batch | refuses: `NotImplementedError: No support for ALTER of constraints in SQLite dialect` |
| `batch.add_column` with an inline `ForeignKey` | `ValueError: Constraint must have a name` unless a `naming_convention` is in play — then **rebuild** |
| `batch.add_column` then `batch.create_foreign_key` | **rebuild** |
| any constraint or column-type change | **rebuild** |

So a plain column is cheap forever and *every* new foreign-key column costs one
rebuild. There is no way around that through Alembic: it classifies a foreign key as
a constraint operation, and its SQLite dialect does not implement ALTER of
constraints. Note that this is Alembic being conservative rather than SQLite
refusing — raw SQLite accepts
`ALTER TABLE things ADD COLUMN owner_id INTEGER REFERENCES users(id)` quite happily.
Reaching for `op.execute` to exploit that is not worth it: it is hand-written DDL
(rule 1), it skips the model Alembic reasons about, and it saves 93 ms.

Which is the other half of the answer — the rebuild is cheap. Measured on a
library-shaped `files` table: **10 ms for 10,000 rows, 93 ms for 100,000.** Table
size is not a reason to avoid a foreign key at this product's scale, and "we will add
the constraint later" is how the schemas diverged in the first place.

### Rebuilding needs foreign keys off, and a check afterwards

The rebuild drops the original table. If another table references it —
`print_jobs.file_id → files.id` — that `DROP TABLE` fails with
`IntegrityError: FOREIGN KEY constraint failed` while enforcement is on. Verified
both ways: with `PRAGMA foreign_keys=OFF` the rebuild succeeds, the other table's
foreign key still points at the right table, rows are intact and
`PRAGMA foreign_key_check` comes back empty.

This is SQLite's own prescribed procedure for altering a table, and its first and
last steps are the pragmas:

```python
def upgrade() -> None:
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"
    if is_sqlite:
        # Outside any transaction: SQLite ignores this pragma while one is open, so
        # setting it inside `op.get_bind().begin()` silently does nothing.
        bind.exec_driver_sql("PRAGMA foreign_keys=OFF")
    try:
        with op.batch_alter_table("files", copy_from=_files_table()) as batch:
            batch.create_foreign_key("fk_files_deleted_by_users", "users",
                                     ["deleted_by"], ["id"])
    finally:
        if is_sqlite:
            bind.exec_driver_sql("PRAGMA foreign_keys=ON")
            orphans = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if orphans:
                raise RuntimeError(f"rebuild left orphaned rows: {orphans}")
```

Two details that bite:

- **`copy_from=`**, not reflection, for new migrations. Batch mode reconstructs the
  table from what it can see, and anything reflection misses is silently dropped on
  the floor. Pass a `Table` defined literally in the migration file, so it is pinned
  to that revision rather than following the models as they move on.

  All 78 existing `batch_alter_table` calls in this chain rely on reflection, and
  none is going to be edited — they have already run on real installations
  (rule 2). The rule is forward-looking: it is cheap in a new migration and it is
  the difference between a rebuild that preserves the table and one that quietly
  simplifies it.
- **The pragma must be outside a transaction.** SQLite ignores
  `PRAGMA foreign_keys` while one is open. This is the same trap that left the test
  suite with enforcement off for months (`tests/conftest.py::_truncate_all`).

### Constraint names: the naming convention is load-bearing

`SQLModel.metadata` carries a `naming_convention` (declared in `app/db/models.py`),
and it is not cosmetic. Batch mode alters a constraint by dropping it **by name** and
recreating it, so an anonymous constraint cannot be altered on SQLite at all —
`batch_alter_table` fails with `ValueError: Constraint must have a name`. The
convention is what makes a schema migratable on the database this product ships with.

It also makes the two schemas comparable: without it, `create_all` and the chain
generate different names for the same constraint, and the parity test cannot tell
that apart from real divergence.

Declared constraints may still name themselves, and most in `app/db/models.py` do:
17 unique constraints, 4 check constraints, 4 indexes. The convention fills in the
rest — every foreign key, every primary key, and every index that comes from a
`Field(index=True)`.

`tests/repo/test_schema_ddl.py::TestConstraintNaming` is the tripwire. It asserts
the convention is still attached to the metadata, that no constraint or index is
anonymous, and that each name carries the prefix for its kind (`pk_`, `fk_`, `uq_`,
`ck_`; an index takes `ix_` or, for the hand-declared partial unique indexes,
`uq_`). A model added without a name that the convention cannot fill fails there
rather than three releases later inside someone's `batch_alter_table`.

### `copy_from` versus reflection — they do different things

Batch mode without `copy_from` **reflects** the table: it rebuilds it in the shape the
database currently has, and applies only the operations the migration names. With
`copy_from=<Table>`, the table instead *becomes* the definition you pass.

That is a difference in behaviour, not just in safety. Measured on a table whose
database shape was missing a foreign key the models declare:

| | Result |
| --- | --- |
| reflection + `create_foreign_key("fk_files_deleted_by_users")` | 2 foreign keys — the missing one stayed missing |
| `copy_from=<literal Table with all three>` | 3 foreign keys — the table converged to the definition |

**But `copy_from` is not the tool for converging a whole schema.** It would mean
hand-writing a literal `Table` for every affected table — 31 of them here, each a
chance to omit a column and have the rebuild silently drop it. Autogenerate naming
every difference as an explicit operation reaches the same end state and is
reviewable line by line, which is why `6acea2a5e555` uses it. The proof of
correctness is not the mechanism; it is that
`migrate._orphan_schema_issues()` returns `[]` afterwards.

So pick by intent:

- **Reflection** when you are changing one thing and want everything else preserved
  exactly as it is. Smallest blast radius. This is what
  `eb8435c9400e_add_missing_audit_foreign_keys` uses: it adds eighteen foreign keys
  and deliberately does *not* sweep up the enum-representation and server-default
  differences autogenerate also offered.
- **`copy_from`** for *one* table that must become a known shape, or when reflection
  cannot see something (an odd server default, a check constraint an older SQLAlchemy
  misses). Define the `Table` **literally in the migration file**, never imported from
  the models: a migration is pinned to a revision, and the models will move on
  without it.

`copy_from` is a per-call argument. There is no Alembic setting that turns it on
globally, and the 78 existing `batch_alter_table` calls in this chain all use
reflection. They will not be changed — they have already run on real installations
(rule 2) — so this is a rule for new work.

**One more thing `copy_from` buys, which is easy to miss:** offline rendering.
`alembic upgrade --sql` cannot run a reflecting batch migration on SQLite at all —

```
This operation cannot proceed in --sql mode; batch mode with dialect sqlite requires
a live database connection with which to reflect the table "collections".
```

so a SQLite batch migration without `copy_from` is one an operator cannot review as DDL
before applying. This repo does not pay that cost — 38 literal `Table` definitions to buy
offline rendering on the dialect where change-control review is least likely — and
`test_offline_sqlite_is_unavailable_while_batch_mode_reflects` pins the decision so it is
a known cost rather than a surprise. If your migration will be applied through a process
that reviews generated SQL, that is the argument for `copy_from`.

### Repair before you constrain

Adding a constraint to rows that already violate it leaves a database that cannot be
written to. SQLite adds the constraint without validating existing rows, because
enforcement is off for the rebuild, so the violation sits there until something
touches the row and then fails at the worst possible moment.

So a constraint migration has three parts, in this order:

1. **Repair.** Null the references that point at rows which are not there. Every
   column `eb8435c9400e` touches is nullable, which is what makes this free: an audit
   pointer (`created_by`, `updated_by`, `deleted_by`) to a user id that does not exist
   carries nothing that nulling destroys.
2. **Constrain**, in batch mode.
3. **Verify**, and *scope the verification to what you added*.
   `PRAGMA foreign_key_check` is the obvious tool and the wrong one: with no argument
   it walks the whole database, and even given a table it reports every violation of
   every constraint on it. The migration test data alone has `print_jobs` rows pointing
   at models that do not exist — violations that predate the migration and are none of
   its business. Reporting them would turn an unrelated inconsistency into a failed
   upgrade. Ask instead the same question the repair asked, per constraint you added,
   and expect no answer.

### The whole thing, end to end, on SQLite

What actually happens when you add a foreign key, from writing it to a self-hoster
upgrading:

1. You declare it in `app/db/models.py`. `create_all` will now emit it, so **new
   installations get it immediately** — that path builds from the models and stamps
   head, and never replays the chain.
2. You autogenerate a migration. Alembic compares the models against a database and
   emits `with op.batch_alter_table(...)` blocks, because `render_as_batch=True` is
   set for SQLite.
3. You trim it. Autogenerate offers everything it noticed — 890 lines and 242
   operations, the first time this was run — and you keep only what the change is
   about.
4. Repair, constrain, verify (above).
5. On upgrade, each affected table is rebuilt: `CREATE TABLE _alembic_tmp_x` with the
   constraint inline, `INSERT … SELECT` the rows, `DROP TABLE x`, rename. Foreign keys
   must be off for that `DROP`, and Alembic's engine leaves them off. **10 ms per
   10,000 rows, 93 ms per 100,000.**
6. `PRAGMA foreign_keys=ON` is restored per connection by `app/db/session.py`, so the
   constraint is enforced from the next request onwards.
7. The parity test proves the two schemas now agree on it.

The recurring temptation is to skip step 4–5 for one dialect because it is awkward.
That is the whole bug: `if not is_sqlite:` is cheap to write and produces two
products.

## Two paths to `head`, one schema

`run_migrations` has three branches:

| State | What runs | Result |
| --- | --- | --- |
| No tables | `create_all` from the models, then `stamp head` | the models' schema |
| Has `alembic_version` | `upgrade head` — pending migrations only | the models' schema |
| Tables, no version | adopt only if the schema matches the models exactly | fails closed otherwise |

A fresh installation therefore **never replays the chain** and an upgraded one never
runs `create_all` — but as of `eb8435c9400e` and `6acea2a5e555` they arrive at the
same place. `_orphan_schema_issues` on a chain-built database returns `[]`.

It did not used to. The two disagreed in 136 ways, and the third branch above is what
made that expensive: it adopts an unversioned database only when the schema matches
the models exactly, so the rescue path worked on a `create_all` database and on no
upgraded one — useless for exactly the installations most likely to need it.

`tests/integration/db/migrations/test_models_versus_chain.py` holds it there:
`KNOWN_MISSING_IN_CHAIN` and `STRUCTURAL_DIFFERENCE_COUNTS` are both empty, and both
are two-sided, so a migration that changes the schema in a way `create_all` does not
fails immediately rather than in three months.

**Which direction to converge, when they disagree.** Toward the models, because
`create_all` is what every new installation runs — the models are the definition, and
the chain is a way of catching up to them. Converging the other way (writing the
chain's shape into the models) is occasionally right, and `vault_audit_findings.run_id`
was one: the chain had `ondelete="CASCADE"` from the day the table existed and the
models never did, so the models were the ones lagging. Decide per difference, and say
which way you went.

The convergence dropped 53 server defaults the chain had, because the models declare
Python-side defaults instead. That is not a downgrade — no `create_all` installation
ever had them — but it does mean a raw `INSERT` omitting a NOT NULL column now fails
everywhere rather than only on fresh installs. If a server default is wanted, add it
to the *model* and let both paths get it.

## Deleting a row means knowing its children

`foreign_keys=ON` is a production pragma and most foreign keys here have no
`ondelete`, which means `RESTRICT`. A parent delete with a child still pointing at it
does not dangle — it **fails**.

Before writing a delete path, list what references the row:

```python
uv run python -c "
from sqlmodel import SQLModel
import app.db.models  # noqa
for t in SQLModel.metadata.sorted_tables:
    for fk in t.foreign_key_constraints:
        for e in fk.elements:
            if e.column.table.name == 'files':
                print(f'{t.name}.{e.parent.name} ondelete={fk.ondelete}')
"
```

Then handle every one: delete it, or null it if the column is nullable, or let
`ondelete=CASCADE` do it. `hard_delete_file` cleaned up three of five and the two it
missed made purging a file in a print batch fail; `purge_library_index` left
`files.external_library_id` pointing at the library it then deleted. Both were 500s on
a fresh installation and dangling rows on an upgraded one.

**When the database cascades, let it — but tell the ORM.** A DB-level
`ON DELETE CASCADE` removes the row without SQLAlchemy knowing, so a caller holding
the session keeps reading an object that no longer exists. Delete the child through
the session, flush, then delete the parent: the identity map stays honest and the
cascade has no row left to race for.

## Queries

- **Soft-deleted rows go through `app.db.scopes`.** `live(Model)` and
  `trashed(Model)`, never a hand-written `deleted_at.is_(None)` — the scopes are the
  single place the rule lives, and a query that spells it out by hand is a query that
  will not follow when the rule changes.
- **Sessions come from `get_session_factory()`**, never a module-level engine. That
  is the seam the test suite overrides and the cloud deployment replaces
  (AGENTS.md hard rule 5).
- **Writes from worker threads use their own session.** `asyncio.to_thread` work must
  not share a session with the request that started it, and a write that lands after
  the caller has read is how a terminal state gets overwritten by a stale snapshot.

## Verifying a schema change

```bash
cd backend
uv run alembic upgrade head                       # forwards
uv run alembic downgrade -1 && uv run alembic upgrade head   # and back
uv run alembic upgrade <prev>:head --sql          # renders without a database
./scripts/test.sh coverage                        # includes the parity tests below
```

The `--sql` render is not optional politeness. It is how an operator reviews DDL before
letting it near their data, and a migration that reaches for the connection — to read a
pragma, to count rows — dies there with `MockConnection has no attribute
exec_driver_sql`. Both convergence migrations did, until the tests below caught it: use
`op.execute` for statements that must appear in the output, and guard connection reads
with `if op.get_context().as_sql: return`.

Three tests are the schema's own guard rails:

- `tests/repo/test_migration_patterns.py` — no constraint operation is skipped for a
  dialect.
- `tests/integration/db/migrations/test_models_versus_chain.py` — the migrated schema
  differs from the models only as recorded, foreign key by foreign key and category
  by category.
- `tests/repo/test_db_parity.py` — the test database enforces what production's does.

A migration that changes the schema and leaves all three green has changed both
supported installations the same way. One that turns any of them red has not.
