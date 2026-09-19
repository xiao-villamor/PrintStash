"""Bounded local model hints. No inputs, credentials or durable tasks live here."""

import re
import threading
from collections import OrderedDict


class WarmupRequests:
    def __init__(self):
        self._lock = threading.Lock()
        self._pending: OrderedDict[str, None] = OrderedDict()

    def request(self, identity: str):
        if not re.fullmatch(r"[0-9a-f]{64}", identity):
            raise ValueError("embedding_model_identity_invalid")
        with self._lock:
            self._pending[identity] = None
            self._pending.move_to_end(identity)
            while len(self._pending) > 4:
                self._pending.popitem(last=False)

    def take(self) -> str | None:
        with self._lock:
            return self._pending.popitem(last=False)[0] if self._pending else None

    def clear(self):
        with self._lock:
            self._pending.clear()


requests = WarmupRequests()
