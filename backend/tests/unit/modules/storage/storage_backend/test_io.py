"""Interrupted downloads cannot publish partially prepared local files."""

from io import BytesIO

import pytest

from app.core import cancellation
from app.modules.storage.storage_backend.io import _copy_stream_create_only


class TestCopyStream:
    def test_withdrawal_cleans_staged_download(self, tmp_path, monkeypatch):
        now = [0.0]
        monkeypatch.setattr(cancellation.time, "monotonic", lambda: now[0])

        class WithdrawnStream(BytesIO):
            def read(self, size=-1):
                data = super().read(size)
                now[0] += 1
                return data

        destination = tmp_path / "copy.stl"
        source = WithdrawnStream(bytes(3 * 1024**2))
        with cancellation.cancellation_scope(lambda: now[0] >= 1):
            with pytest.raises(cancellation.OperationCancelled):
                _copy_stream_create_only(source, destination)
        assert list(tmp_path.iterdir()) == []
