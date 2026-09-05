"""Retry copies enforce exact size and digest before use."""

import hashlib
import io

import pytest

from app.db.models import BackupRun
from app.services.backup_replica_retry import RetryRefused, _copy_exact


class TestExactCopy:
    @pytest.mark.parametrize("payload", [b"short", b"expected-plus-extra", b"replaced"])
    def test_rejects_unexpected_stream_bytes(self, tmp_path, payload):
        run = BackupRun(
            id="r",
            backup_id="b",
            archive_name="archive.tar.gz",
            size_bytes=8,
            archive_sha256=hashlib.sha256(b"expected").hexdigest(),
        )
        with pytest.raises(RetryRefused, match="backup_retry_source_changed"):
            _copy_exact(io.BytesIO(payload), tmp_path / "copy", run)
        assert (tmp_path / "copy").stat().st_size <= 8
