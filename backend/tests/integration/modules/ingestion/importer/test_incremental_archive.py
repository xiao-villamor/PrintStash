"""An archive publishes each source before requesting its next extracted member."""

import zipfile

import printstash_core.files
from sqlmodel import select

from app.db.models import File
from app.db.session import get_session_factory
from app.modules.ingestion import importer
from app.modules.storage.storage_backend.runtime import get_backend
from app.runtime.jobs import registry


class TestIncrementalArchiveContract:
    def test_saves_the_first_archive_source_before_extracting_the_next(
        self, local_storage, tmp_path, monkeypatch
    ):
        archive = tmp_path / "batch.zip"
        with zipfile.ZipFile(archive, "w") as output:
            output.writestr("first.gcode", "G28\n")
            output.writestr("second.gcode", "G29\n")
        extract = printstash_core.files.iter_selected
        observed = []

        def observe(*args, **kwargs):
            iterator = extract(*args, **kwargs)
            try:
                yield next(iterator)
                with get_session_factory().scoped_session() as session:
                    files = session.exec(
                        select(File).where(File.ingestion_key.is_not(None))
                    ).all()
                    assert len(files) == 1
                    assert files[0].original_filename == "first.gcode"
                    assert get_backend().exists(files[0].path)
                    observed.append(files[0].id)
                yield from iterator
            finally:
                iterator.close()

        monkeypatch.setattr(printstash_core.files, "iter_selected", observe)
        job = registry.create(kind="archive")
        importer.import_archive(
            job_id=job,
            archive_path=archive,
            names=["first.gcode", "second.gcode"],
            collection=None,
            tags=None,
            source_url=None,
            actor_user_id=None,
            session_factory=get_session_factory(),
        )
        assert len(observed) == 1
        assert registry.get(job).succeeded == 2
