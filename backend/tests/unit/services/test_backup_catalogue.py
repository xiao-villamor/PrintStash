"""Display merging never hides ambiguous archives or broadens exact selection."""

import pytest

from app.services.backup import BackupMeta
from app.services.backup_catalogue import BackupCatalogue, BackupIdentityConflictError


def _source(location, path, *, digest="a" * 64, identity="archive"):
    return BackupMeta(
        id=identity,
        created_at="2026-01-01T00:00:00Z",
        size_bytes=10,
        storage_backend="local",
        file_count=0,
        app_version="1",
        path=path,
        location=location,
        archive_sha256=digest,
        source_ref=f"{location}:{path}",
    )


class TestBackupCatalogue:
    def test_equal_replicas_have_one_display_winner_but_keep_exact_sources(self):
        local = _source("local", "/backup/archive.tar.gz")
        remote = _source("opendal:s3", "s3/bucket/archive.tar.gz")
        catalogue = BackupCatalogue([remote, local])
        assert [row.source_ref for row in catalogue.backups()] == [local.source_ref]
        assert len(catalogue.sources()) == 2
        assert (
            catalogue.select("archive", source_ref=remote.source_ref).path
            == remote.path
        )
        assert local.canonical is False

    @pytest.mark.parametrize("digest", [None, "b" * 64])
    def test_ambiguous_ids_require_exact_selection(self, digest):
        first = _source("local", "/backup/first", digest=digest)
        second = _source("s3", "printstash-backups/second")
        catalogue = BackupCatalogue([first, second])
        assert len(catalogue.backups()) == 2
        assert not any(row.canonical for row in catalogue.sources())
        with pytest.raises(
            BackupIdentityConflictError, match="backup_identity_conflict"
        ):
            catalogue.select("archive")
        assert (
            catalogue.select("archive", source_ref=second.source_ref).path
            == second.path
        )
        assert catalogue.select("archive", source_ref="unknown") is None

    def test_provider_precedence_is_stable_across_input_order(self):
        sources = [
            _source("s3", "nexus3d-backups/old"),
            _source("opendal:webdav", "webdav/replica"),
            _source("s3", "printstash-backups/current"),
        ]
        catalogue = BackupCatalogue(sources)
        assert catalogue.select("archive").path == "printstash-backups/current"
        assert [row.precedence for row in catalogue.sources()] == [0, 1, 2]

    def test_unknown_historical_identity_remains_selectable_when_unambiguous(self):
        source = _source("local", "/old/archive", digest=None)
        assert BackupCatalogue([source]).select("archive").path == source.path
        assert BackupCatalogue([]).select("missing") is None
