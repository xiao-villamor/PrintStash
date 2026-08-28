"""Reading the queue, scoped to what the caller may see.

The queue is fleet-wide but printer permissions are not, so the listing takes the
printer ids the caller has a role on and shows only jobs assigned to them. The
case worth pinning is the empty one: a caller with no printer roles must get an
empty queue, not the whole fleet's.

That direction matters because the obvious implementation of "filter by these
ids" degenerates on an empty set. A `WHERE printer_id IN ()` that is optimised
away, or a falsy check that skips the filter entirely, both turn "you may see
nothing" into "you may see everything" — and nothing else in the request would
notice.
"""

from __future__ import annotations

from sqlmodel import Session

from app.services import fleet


class TestListQueuePage:
    def test_shows_nothing_to_a_caller_with_no_printer_roles(
        self, db_session: Session
    ) -> None:
        # The dangerous degeneration: an empty allow-list read as "no filter".
        assert fleet.list_queue_page(db_session, visible_printer_ids=set()) == []


class TestListQueue:
    def test_reports_an_empty_queue_when_nothing_is_queued(
        self, db_session: Session
    ) -> None:
        assert fleet.list_queue(db_session) == []
