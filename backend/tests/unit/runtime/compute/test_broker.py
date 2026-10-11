"""Retired compute tickets never consume another member's execution or output."""

import json

import pytest

from app.runtime.compute.broker import Ticket, active_tickets
from app.runtime.compute.protocol import Priority, RenderRequest


@pytest.fixture
def ticket_factory():
    def make_ticket(name, *, deadline=100):
        return Ticket(
            RenderRequest(
                deadline=deadline,
                priority=Priority.BACKGROUND,
                request_id=name,
                payload="",
                recipe="test",
                units=1,
            ),
            received=1,
        )

    return make_ticket


class TestActiveTickets:
    def test_preserves_live_ticket_order(self, ticket_factory):
        first = ticket_factory("first")
        second = ticket_factory("second")

        active = active_tickets([first, second], now=50)

        assert active == [first, second]
        assert first.result is second.result is None
        assert not first.ready.is_set()
        assert not second.ready.is_set()

    def test_retires_only_expired_members(self, ticket_factory):
        expired = ticket_factory("expired", deadline=50)
        live = ticket_factory("live")

        active = active_tickets([expired, live], now=50)

        assert active == [live]
        assert json.loads(expired.result) == {"error": "deadline"}
        assert expired.ready.is_set()
        assert not live.ready.is_set()

    def test_retires_only_cancelled_members(self, ticket_factory):
        cancelled = ticket_factory("cancelled")
        cancelled.cancelled.set()
        live = ticket_factory("live")

        active = active_tickets([cancelled, live], now=50)

        assert active == [live]
        assert json.loads(cancelled.result) == {"error": "cancelled"}
        assert cancelled.ready.is_set()
        assert not live.ready.is_set()

    def test_retires_a_fully_cancelled_group(self, ticket_factory):
        cancelled = ticket_factory("cancelled")
        cancelled.cancelled.set()

        active = active_tickets([cancelled], now=50)

        assert active == []
        assert json.loads(cancelled.result) == {"error": "cancelled"}
        assert cancelled.ready.is_set()

    def test_releases_retired_geometry_before_waking_handler(self):
        import weakref

        import numpy as np

        from app.runtime.compute.protocol import BinaryRenderRequest

        request = BinaryRenderRequest(
            deadline=100,
            priority=Priority.BACKGROUND,
            request_id="cancelled",
            recipe="test",
            units=1,
            geometry_key="0" * 64,
            geometry_bytes=24,
            header="{}",
        )
        request._decoded = (np.zeros(3),)
        retired = weakref.ref(request._decoded[0])
        from threading import Event

        class Completion(Event):
            released = False

            def set(self):
                self.released = retired() is None
                super().set()

        completion = Completion()
        ticket = Ticket(request, received=1, ready=completion)
        ticket.cancelled.set()

        active = active_tickets([ticket], now=50)

        assert active == []
        assert completion.is_set()
        assert completion.released
