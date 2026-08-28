"""Shared machinery every builder uses: unique identities and one save path.

Two things in here exist to remove decisions from the person writing a test.

**Unique identities.** Almost every library table has a unique column — a model
slug, a model hash, an artifact `sha256`, a printer name. A test that hard-codes
one works alone and collides the moment a second row is built, which is why
nearly every hand-rolled helper in this suite grew its own `made = {"n": 0}`
counter. `nth()` is that counter, once, and the autouse `reset_factory_counters`
fixture in `tests/conftest.py` rewinds it per test, so the generated values are
the same whether you run one test or the whole file. That matters when a failure
message says `model-3`.

**One save path.** `save()` adds, commits and refreshes. Builders commit rather
than flush because the production code under test frequently opens its own
transaction, and a row that exists only in a pending flush is invisible to it —
that mismatch was the cause of several "works in isolation" bugs in the old
helpers. A test that specifically needs an *uncommitted* row should build the
model object directly; that is rare enough to be worth spelling out inline.
"""

from __future__ import annotations

from typing import TypeVar

from sqlmodel import Session

_counters: dict[str, int] = {}

RowT = TypeVar("RowT")


def nth(kind: str) -> int:
    """The next sequence number for *kind*, starting at 1.

    Per-test, not per-session: `reset_factory_counters` clears these between
    tests so a generated slug is reproducible from the test alone.
    """
    _counters[kind] = _counters.get(kind, 0) + 1
    return _counters[kind]


def reset_counters() -> None:
    """Rewind every sequence. Called by the autouse fixture, not by tests."""
    _counters.clear()


def unique_hash(kind: str) -> str:
    """A 64-character hex identity, unique per call and readable in a failure.

    `models.hash` and `files.sha256` are both unique 64-char columns. The number
    is zero-padded rather than random so `0…0003` tells you it was the third one
    built, and so a diff between two runs of the same test is empty.
    """
    return f"{nth(kind):064d}"


def reject_aliases(overrides: dict[str, object], aliases: dict[str, str]) -> None:
    """Fail clearly when a caller passes a column the builder owns.

    Every builder forwards `**overrides` to its model, which is what keeps a
    one-off field at the call site instead of in the builder. The cost is that a
    keyword the builder *also* sets — `is_superuser` when the builder took
    `superuser` — reaches the model constructor twice and surfaces as
    `got multiple values for keyword argument`, pointing at SQLModel rather than
    at the line that needs changing.

    Naming the aliases turns that into a message that says what to write. Add an
    entry whenever a builder's keyword differs from its column, which is exactly
    where a caller is most likely to guess the column name.
    """
    for wrong, right in aliases.items():
        if wrong in overrides:
            raise TypeError(
                f"pass `{right}=` to this builder, not `{wrong}=` "
                f"(the builder owns that column)"
            )


def save(session: Session, row: RowT) -> RowT:
    """Persist one row and return it with its database-assigned id populated."""
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
