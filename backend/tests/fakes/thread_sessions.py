"""Real SQL sessions that expose request ownership violations to ASGI tests."""

from __future__ import annotations

from contextlib import contextmanager
from threading import Lock, get_ident
from typing import Any, Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session


class _ThreadBoundSession(Session):
    def __init__(self, factory: ThreadBoundSessionFactory) -> None:
        super().__init__(factory.engine)
        self._factory = factory
        self._owner = get_ident()
        self._closed_once = False
        event.listen(self, "do_orm_execute", self._check_event)
        event.listen(self, "before_flush", self._check_event)
        event.listen(self, "after_begin", self._check_event)

    def _check_owner(self) -> None:
        assert get_ident() == self._owner, "SQL Session crossed its creating thread"

    def _check_event(self, *_args: Any) -> None:
        self._check_owner()

    def get(self, *args: Any, **kwargs: Any) -> Any:
        self._check_owner()
        return super().get(*args, **kwargs)

    def commit(self) -> None:
        self._check_owner()
        super().commit()

    def rollback(self) -> None:
        self._check_owner()
        super().rollback()

    def close(self) -> None:
        self._check_owner()
        super().close()
        if not self._closed_once:
            self._closed_once = True
            self._factory._closed()


class ThreadBoundSessionFactory:
    """Track only command sessions; the test's arrange session stays independent."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self._lock = Lock()
        self._active = 0
        self._opened = 0
        self._threads: set[int] = set()

    @property
    def active_count(self) -> int:
        with self._lock:
            return self._active

    @property
    def opened_count(self) -> int:
        with self._lock:
            return self._opened

    @property
    def thread_ids(self) -> frozenset[int]:
        with self._lock:
            return frozenset(self._threads)

    def session(self) -> Session:
        session = _ThreadBoundSession(self)
        with self._lock:
            self._active += 1
            self._opened += 1
            self._threads.add(get_ident())
        return session

    def _closed(self) -> None:
        with self._lock:
            self._active -= 1
            assert self._active >= 0, "Session closed without its creation"

    @contextmanager
    def scoped_session(self) -> Iterator[Session]:
        session = self.session()
        try:
            yield session
        finally:
            session.close()

    def dispose(self) -> None:
        self.engine.dispose()
