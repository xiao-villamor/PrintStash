"""Record the SQL a block of code executes, to test how a read scales.

A test that seeds ten rows cannot see an O(n²) loop or a query that binds one
parameter per row: both are fast at ten. What it can see is the *shape* of the
work, which does not depend on the machine: how many statements ran, and how
many parameters the largest one bound. Asserting that neither grows with the
data is a deterministic scaling test that is cheap enough for every PR (#295).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import Engine, event


@dataclass(frozen=True)
class Statement:
    sql: str
    bound_parameters: int


@dataclass
class StatementLog:
    statements: list[Statement] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.statements)

    @property
    def max_bound_parameters(self) -> int:
        return max((s.bound_parameters for s in self.statements), default=0)

    @contextmanager
    def recording(self) -> Iterator[StatementLog]:
        """Record every statement any engine executes until the block exits."""
        self.statements.clear()

        def record(conn, cursor, statement, parameters, context, executemany):
            # executemany binds one row's parameters per execution.
            row = parameters[0] if executemany and parameters else parameters
            bound = len(row) if isinstance(row, (tuple, list, dict)) else 0
            self.statements.append(Statement(statement, bound))

        event.listen(Engine, "before_cursor_execute", record)
        try:
            yield self
        finally:
            event.remove(Engine, "before_cursor_execute", record)
