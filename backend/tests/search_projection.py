"""Explicitly advance durable projections in tests that require indexed content."""

from app.modules.search.projection import process_pending


def drain_search(session):
    session.commit()
    for _ in range(2048):
        worked = process_pending(session)
        session.commit()
        if not worked:
            session.expire_all()
            return
    raise AssertionError("search projection did not become idle")
