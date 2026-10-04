"""Hold a real producer at its external storage boundary after admission."""

from contextlib import contextmanager
from threading import Event

from app.modules.storage.storage_backend.local import LocalStorageBackend


class GatedDerivativeStorage(LocalStorageBackend):
    def __init__(self, *, held_key: str, admitted: Event, release: Event):
        super().__init__()
        self.held_key = held_key
        self.admitted = admitted
        self.release = release

    @contextmanager
    def local_path(self, key, *, directory=None):
        if key == self.held_key:
            self.admitted.set()
            if not self.release.wait(30):
                raise TimeoutError("test storage gate was not released")
        with super().local_path(key) as path:
            yield path
