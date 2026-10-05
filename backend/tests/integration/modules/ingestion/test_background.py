"""Import handlers report durable progress and preserve staged review manifests.

Each handler runs as the step of an ``ingest.*`` Job, against the
``IngestRequest`` that Job owns; a review manifest is written back onto that
request, so any process can serve the later selection.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid as _uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session, select

import app.modules.ingestion.background as ingest_background
from app.core.config import _overlay, settings
from app.db.models import File, IngestRequest, User
from app.db.session import get_session_factory
from app.modules.ingestion import batch_store, import_resolvers, importer
from app.modules.ingestion.batch_contracts import EntrySpec, RemoteSource
from app.modules.ingestion.importer import ImportError_
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.work.jobs import jobs
from tests._env import use_local_storage
from tests.factories import (
    build_user,
)
from tests.factories.ops import build_job_context
from tests.factories.protocols import MakeIngestRequest


@pytest.fixture
def owner(db_session: Session) -> User:
    """The user this module's Jobs belong to.

    `jobs.owner_user_id` is a foreign key, so a job owned by a user id that does
    not exist is refused — exactly as it is in production. These tests are about
    progress and manifests rather than about users, so the owner is a fixture and
    the tests name it rather than hardcoding an id.
    """
    return build_user(db_session, "import-owner")


@pytest.fixture
def job_id(owner: User, make_ingest_request: MakeIngestRequest) -> str:
    """The Job an accepted request created; the handler runs as its step."""
    return make_ingest_request(owner).job_id


class TestCollectionTarget:
    def test_uses_the_capture_title_when_no_parent_is_given(self) -> None:
        assert ingest_background.collection_target(None, "My Model") == "My Model"

    def test_nests_the_title_under_the_chosen_parent(self) -> None:
        assert ingest_background.collection_target("Parent/", "Child") == "Parent/Child"

    @pytest.mark.parametrize(("parent", "title"), [(None, "  "), ("  ", "  ")])
    def test_falls_back_to_a_generic_name_when_the_title_is_blank(
        self, parent: str | None, title: str
    ) -> None:
        # A blank title comes from a page we could not read a name off. An empty
        # collection path would import into the vault root instead, silently
        # scattering the capture across the library.
        assert (
            ingest_background.collection_target(parent, title) == "Imported collection"
        )


@pytest.fixture
def download_batch(owner, job_id, tmp_path):
    use_local_storage(tmp_path)
    batch = importer.begin_batch(
        job_context=build_job_context(job_id),
        collection=None,
        tags=None,
        source_url=None,
        actor_user_id=owner.id,
        session_factory=get_session_factory(),
    )
    batch.begin(None)
    return batch


class TestDownloadAndCollect:
    @pytest.mark.asyncio
    async def test_skips_a_direct_file_that_is_not_importable(
        self, download_batch, tmp_path, monkeypatch
    ):
        staged = tmp_path / "readme.txt"
        staged.write_bytes(b"not a model")
        download = AsyncMock(return_value=(staged, staged.name))
        monkeypatch.setattr(importer, "download_to_staging", download)
        url = "https://cdn.test/readme.txt"
        spec = EntrySpec(url, staged.name, RemoteSource(url), None)

        await ingest_background._consume_download(
            download_batch, url, spec=spec, source_url=None
        )

        assert batch_store.counts(download_batch.owner).skipped == 1
        assert not staged.exists()
        download.assert_awaited_once_with(
            url, window_max_bytes=settings.ingestion_batch_max_mb * 1024 * 1024
        )

    @pytest.mark.asyncio
    async def test_extracts_the_entries_of_a_zip(
        self, download_batch, tmp_path, monkeypatch
    ):
        body = _cube_stl_bytes()
        staged = tmp_path / "bundle.zip"
        staged.write_bytes(_zip_bytes(entry="cube.stl", content=body))
        monkeypatch.setattr(
            importer,
            "download_to_staging",
            AsyncMock(return_value=(staged, staged.name)),
        )
        url = "https://cdn.test/bundle.zip"
        spec = EntrySpec(url, staged.name, RemoteSource(url), None)

        await ingest_background._consume_download(
            download_batch, url, spec=spec, source_url=None
        )

        record = batch_store.results(download_batch.owner, limit=1)[0]
        assert record.published and record.spec.display_name == "cube.stl"
        with get_session_factory().scoped_session() as session:
            file = session.get(File, record.file_id)
            assert file is not None and file.original_filename == "cube.stl"
            assert get_backend().read_bytes(file.path) == body
        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_returns_a_direct_mesh_file_unchanged(
        self, download_batch, tmp_path, monkeypatch
    ):
        body = _cube_stl_bytes()
        staged = tmp_path / "cube.stl"
        staged.write_bytes(body)
        monkeypatch.setattr(
            importer,
            "download_to_staging",
            AsyncMock(return_value=(staged, staged.name)),
        )
        url = "https://cdn.test/cube.stl"
        spec = EntrySpec(url, staged.name, RemoteSource(url), None)

        await ingest_background._consume_download(
            download_batch, url, spec=spec, source_url=None
        )

        record = batch_store.results(download_batch.owner, limit=1)[0]
        assert record.published
        with get_session_factory().scoped_session() as session:
            file = session.get(File, record.file_id)
            assert file is not None and file.original_filename == "cube.stl"
            assert get_backend().read_bytes(file.path) == body
        assert not staged.exists()


class TestStageMembers:
    @pytest.mark.asyncio
    async def test_stage_members_isolates_per_member_failures(
        self, owner, job_id, tmp_path, monkeypatch
    ):
        use_local_storage(tmp_path)
        staged = tmp_path / "cube.stl"
        body = _cube_stl_bytes()
        staged.write_bytes(body)
        members = [
            import_resolvers.CollectionMember(
                source_id="1", title="Good", page_url="https://ok.test/model"
            ),
            import_resolvers.CollectionMember(
                source_id="2", title="Bad", page_url="https://bad.test/model"
            ),
            import_resolvers.CollectionMember(
                source_id="3", title="Crashy", page_url="https://crash.test/model"
            ),
        ]
        monkeypatch.setattr(
            import_resolvers,
            "resolve_page_url",
            AsyncMock(
                side_effect=[
                    None,
                    ImportError_("member_resolve_failed"),
                    RuntimeError("boom"),
                ]
            ),
        )
        download = AsyncMock(return_value=(staged, staged.name))
        monkeypatch.setattr(importer, "download_to_staging", download)

        await ingest_background.run_collection_member_import(
            job_context=build_job_context(job_id),
            members=members,
            target_collection="Imported",
            tags=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )

        status = jobs.get(job_id)
        assert status is not None and status.state == "completed"
        assert status.succeeded == 1 and status.failed == 2
        items = {item["name"]: item for item in status.result["items"]}
        assert items["Bad"]["error"] == "member_resolve_failed"
        assert items["Crashy"]["error"] == "RuntimeError"
        with get_session_factory().scoped_session() as session:
            file = session.get(File, items["Good"]["file_id"])
            assert file is not None and get_backend().read_bytes(file.path) == body
        download.assert_awaited_once_with(
            members[0].page_url,
            window_max_bytes=settings.ingestion_batch_max_mb * 1024 * 1024,
        )

    @pytest.mark.asyncio
    async def test_stage_members_reports_no_importable_files_without_error(
        self, owner, job_id, tmp_path, monkeypatch
    ):
        use_local_storage(tmp_path)
        staged = tmp_path / "readme.txt"
        staged.write_bytes(b"not a model")
        member = import_resolvers.CollectionMember(
            source_id="9", title="Empty", page_url="https://empty.test/model"
        )
        monkeypatch.setattr(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            importer,
            "download_to_staging",
            AsyncMock(return_value=(staged, staged.name)),
        )

        await ingest_background.run_collection_member_import(
            job_context=build_job_context(job_id),
            members=[member],
            target_collection="Imported",
            tags=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )

        status = jobs.get(job_id)
        assert status is not None and status.state == "failed"
        assert status.error == "no_importable_files" and status.skipped == 1
        assert status.succeeded == 0 and status.result["items"] == []
        assert not staged.exists()


class TestHandleCollectionUrl:
    @pytest.mark.asyncio
    async def test_handle_collection_url_review_stages_manifest(
        self, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        from app.schemas.ingest import UrlIngestRequest

        req = UrlIngestRequest(url="https://printables.com/collections/9", review=True)
        members = [
            import_resolvers.CollectionMember(
                page_url="https://printables.com/model/1", title="A", source_id="1"
            )
        ]
        with patch.object(
            import_resolvers,
            "resolve_collection_url",
            AsyncMock(return_value=("Cool Collection", members)),
        ):
            await ingest_background._handle_collection_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert status.result["kind"] == "collection_manifest"
        assert status.result["collection_name"] == "Cool Collection"
        assert len(status.result["members"]) == 1

    @pytest.mark.asyncio
    async def test_handle_collection_url_auto_imports_members(
        self, owner, tmp_path, job_id, monkeypatch
    ):
        from app.schemas.ingest import UrlIngestRequest

        use_local_storage(tmp_path)
        req = UrlIngestRequest(url="https://printables.com/collections/9", review=False)
        members = [
            import_resolvers.CollectionMember(
                source_id="1", title="A", page_url="https://printables.com/model/1"
            )
        ]
        staged = tmp_path / "cube.stl"
        body = _cube_stl_bytes()
        staged.write_bytes(body)
        monkeypatch.setattr(
            import_resolvers,
            "resolve_collection_url",
            AsyncMock(return_value=("Cool Collection", members)),
        )
        monkeypatch.setattr(
            import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            importer,
            "download_to_staging",
            AsyncMock(return_value=(staged, staged.name)),
        )

        await ingest_background._handle_collection_url(
            job_context=build_job_context(job_id),
            req=req,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )

        status = jobs.get(job_id)
        assert status is not None and status.state == "completed"
        assert status.succeeded == 1
        with get_session_factory().scoped_session() as session:
            file = session.get(File, status.result["items"][0]["file_id"])
            assert file is not None and get_backend().read_bytes(file.path) == body


class TestImportFromUrl:
    @pytest.mark.asyncio
    async def test_import_from_url_collection_resolve_failure_marks_job_failed(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.schemas.ingest import UrlIngestRequest

        req = UrlIngestRequest(url="https://printables.com/collections/9")
        with (
            patch.object(
                import_resolvers, "classify_collection", return_value="printables"
            ),
            patch.object(
                import_resolvers, "resolve_collection_url", AsyncMock(return_value=None)
            ),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "collection_resolve_failed"

    @pytest.mark.asyncio
    async def test_import_from_url_download_import_error_marks_job_failed(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.schemas.ingest import UrlIngestRequest

        req = UrlIngestRequest(url="https://cdn.test/model.stl")
        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(
                importer,
                "download_to_staging",
                AsyncMock(side_effect=ImportError_("download_failed")),
            ),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "download_failed"

    @pytest.mark.asyncio
    async def test_import_from_url_unexpected_download_error_marks_job_failed(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.schemas.ingest import UrlIngestRequest

        req = UrlIngestRequest(url="https://cdn.test/model.stl")
        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(
                importer,
                "download_to_staging",
                AsyncMock(side_effect=RuntimeError("network blew up")),
            ),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "network blew up"

    @pytest.mark.asyncio
    async def test_import_from_url_non_file_response_reports_not_a_direct_file(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.core.config import settings
        from app.schemas.ingest import UrlIngestRequest

        staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.html"
        staged.write_bytes(b"<html>not a model</html>")
        req = UrlIngestRequest(url="https://example.com/some-page")

        async def fake_download(url: str, *, window_max_bytes: int):
            return staged, "some-page.html"

        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(importer, "download_to_staging", fake_download),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "url_not_a_direct_file"
        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_import_from_url_zip_response_stages_archive_manifest(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.core.config import settings
        from app.schemas.ingest import UrlIngestRequest

        staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.zip"
        staged.write_bytes(_zip_bytes())
        req = UrlIngestRequest(url="https://cdn.test/bundle.zip")

        async def fake_download(url: str, *, window_max_bytes: int):
            return staged, "bundle.zip"

        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(importer, "download_to_staging", fake_download),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert status.result["kind"] == "archive_manifest"

    @pytest.mark.asyncio
    async def test_import_from_url_multi_file_page_stages_files_manifest(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.schemas.ingest import UrlIngestRequest

        req = UrlIngestRequest(url="https://www.printables.com/model/123-x")
        files = [
            import_resolvers.ModelFile(file_id="1", name="a.stl", file_type="stl"),
            import_resolvers.ModelFile(file_id="2", name="b.stl", file_type="stl"),
        ]

        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers,
                "list_model_files",
                AsyncMock(return_value=("Cool Model", files)),
            ),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert status.result["kind"] == "model_files_manifest"
        assert status.result["page_title"] == "Cool Model"
        assert len(status.result["files"]) == 2

    @pytest.mark.asyncio
    async def test_import_from_url_zip_inspect_import_error_marks_job_failed(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.core.config import settings
        from app.schemas.ingest import UrlIngestRequest

        staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.zip"
        staged.write_bytes(_zip_bytes())
        req = UrlIngestRequest(url="https://cdn.test/bundle.zip")

        async def fake_download(url: str, *, window_max_bytes: int):
            return staged, "bundle.zip"

        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(importer, "download_to_staging", fake_download),
            patch.object(
                importer,
                "inspect_archive",
                side_effect=ImportError_("archive_zip_bomb"),
            ),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "archive_zip_bomb"
        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_import_from_url_single_direct_file_imports_successfully(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        from app.core.config import settings
        from app.schemas.ingest import UrlIngestRequest

        staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.stl"
        staged.write_bytes(_cube_stl_bytes())
        req = UrlIngestRequest(url="https://cdn.test/cube.stl")

        async def fake_download(url: str, *, window_max_bytes: int):
            return staged, "cube.stl"

        with (
            patch.object(import_resolvers, "classify_collection", return_value=None),
            patch.object(
                import_resolvers, "list_model_files", AsyncMock(return_value=None)
            ),
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(importer, "download_to_staging", fake_download),
        ):
            await ingest_background.import_from_url(
                job_context=build_job_context(job_id),
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "completed", status.error
        assert status.model_id is not None


class TestInspectUploadedArchive:
    def test_refuses_an_archive_without_importable_files(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)
        with zipfile.ZipFile(staged, "w") as archive:
            archive.writestr("notes.txt", "No models here")

        ingest_background.inspect_uploaded_archive(
            job_context=build_job_context(job_id),
            staged=staged,
            original_filename="staged.zip",
            cancelled=lambda: False,
        )

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == ("failed", "no_importable_files")
        assert staged.exists()

    def test_bounds_cancel_probes_during_archive_review(
        self, db_session, owner, tmp_path, job_id, monkeypatch
    ):
        from app.core.cancellation import time as probe_time
        from app.core.config import settings
        from app.modules.ingestion import staging_leases
        from app.modules.storage.hashing import sha256_file

        use_local_storage(tmp_path)
        staged = settings.incoming_dir / "probe-budget.zip"
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_STORED) as bundle:
            bundle.writestr("mesh.stl", b"x" * (8 * 1024 * 1024))
        staging_leases.create_job_lease(
            db_session,
            job_id=job_id,
            owner_user_id=owner.id,
            path=staged,
            size_bytes=staged.stat().st_size,
            sha256=sha256_file(staged),
            check_capacity=False,
        )
        db_session.commit()
        probes = []
        # Assert throttling independently of disk speed or contention on the host.
        monkeypatch.setattr(probe_time, "monotonic", lambda: 0.0)

        def withdrawn():
            probes.append(True)
            return False

        ingest_background.inspect_uploaded_archive(
            job_context=build_job_context(job_id),
            staged=staged,
            original_filename="probe-budget.zip",
            cancelled=withdrawn,
        )

        assert len(probes) < 8
        assert jobs.get(job_id).state == "completed"
        assert staged.exists()

    def test_reports_prepared_file_count_on_the_job(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        ingest_background.inspect_uploaded_archive(
            job_context=build_job_context(job_id),
            staged=staged,
            original_filename="staged.zip",
            cancelled=lambda: False,
        )

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.processed, status.total) == ("completed", 1, 1)

    def test_stops_before_publishing_a_cancelled_archive(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        ingest_background.inspect_uploaded_archive(
            job_context=build_job_context(job_id),
            staged=staged,
            original_filename="staged.zip",
            cancelled=lambda: True,
        )

        db_session.expire_all()
        request = db_session.get(IngestRequest, job_id)
        assert request is not None
        assert json.loads(request.manifest_json) == {}

    def test_records_the_archive_manifest_on_the_request(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        ingest_background.inspect_uploaded_archive(
            job_context=build_job_context(job_id),
            staged=staged,
            original_filename="staged.zip",
            cancelled=lambda: False,
        )

        db_session.expire_all()
        request = db_session.get(IngestRequest, job_id)
        assert request is not None
        manifest = json.loads(request.manifest_json)
        assert (manifest["kind"], [e["name"] for e in manifest["entries"]]) == (
            "archive",
            ["cube.stl"],
        )

    def test_fails_the_job_on_a_refused_archive(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        with patch.object(
            importer, "inspect_archive", side_effect=ImportError_("archive_zip_bomb")
        ):
            ingest_background.inspect_uploaded_archive(
                job_context=build_job_context(job_id),
                staged=staged,
                original_filename="staged.zip",
                cancelled=lambda: False,
            )

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == ("failed", "archive_zip_bomb")

    def test_retains_the_staged_archive_it_refused(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        with patch.object(
            importer, "inspect_archive", side_effect=ImportError_("archive_zip_bomb")
        ):
            ingest_background.inspect_uploaded_archive(
                job_context=build_job_context(job_id),
                staged=staged,
                original_filename="staged.zip",
                cancelled=lambda: False,
            )

        assert staged.exists()

    def test_leaves_an_unexpected_failure_to_the_job_runner(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        with patch.object(
            importer, "inspect_archive", side_effect=RuntimeError("boom")
        ):
            with pytest.raises(RuntimeError, match="boom"):
                ingest_background.inspect_uploaded_archive(
                    job_context=build_job_context(job_id),
                    staged=staged,
                    original_filename="staged.zip",
                    cancelled=lambda: False,
                )

        # Kept for the retry the runner's failure makes possible.
        assert staged.exists()


class TestRunFileSelectionImport:
    @pytest.mark.asyncio
    async def test_run_file_selection_import_reports_import_error(
        self, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        with patch.object(
            import_resolvers,
            "resolve_selected_sources",
            AsyncMock(side_effect=ImportError_("printables_resolve_failed")),
        ):
            await ingest_background.run_file_selection_import(
                job_context=build_job_context(job_id),
                page_url="https://www.printables.com/model/1",
                files=[],
                collection=None,
                tags=None,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "printables_resolve_failed"

    @pytest.mark.asyncio
    async def test_run_file_selection_import_no_files_reports_failure(
        self, owner, tmp_path, job_id, monkeypatch
    ):
        use_local_storage(tmp_path)
        staged = tmp_path / "readme.txt"
        staged.write_bytes(b"not a model")
        selected = import_resolvers.ModelFile("readme", "readme.txt", "other")
        source = import_resolvers.SelectedFileDownload(
            selected, "https://cdn.test/readme.txt"
        )
        monkeypatch.setattr(
            import_resolvers,
            "resolve_selected_sources",
            AsyncMock(return_value=(source,)),
        )
        monkeypatch.setattr(
            importer,
            "download_to_staging",
            AsyncMock(return_value=(staged, staged.name)),
        )

        await ingest_background.run_file_selection_import(
            job_context=build_job_context(job_id),
            page_url="https://www.printables.com/model/1",
            files=[selected],
            collection=None,
            tags=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )

        status = jobs.get(job_id)
        assert status is not None and status.state == "failed"
        assert status.error == "no_importable_files"
        assert status.skipped == 1
        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_run_file_selection_import_reports_unexpected_error(
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        with patch.object(
            import_resolvers,
            "resolve_selected_sources",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await ingest_background.run_file_selection_import(
                job_context=build_job_context(job_id),
                page_url="https://www.printables.com/model/1",
                files=[],
                collection=None,
                tags=None,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "boom"


class TestRunCollectionMemberImport:
    @pytest.mark.asyncio
    async def test_run_collection_member_import_reports_unexpected_error(
        self, owner, tmp_path, job_id, monkeypatch
    ):
        use_local_storage(tmp_path)
        member = import_resolvers.CollectionMember(
            source_id="1", title="Broken", page_url="https://example.com/model"
        )
        monkeypatch.setattr(
            import_resolvers,
            "resolve_page_url",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        await ingest_background.run_collection_member_import(
            job_context=build_job_context(job_id),
            members=[member],
            target_collection="Cool",
            tags=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )

        status = jobs.get(job_id)
        assert status is not None and status.state == "failed"
        assert status.error == "RuntimeError" and status.failed == 1
        assert status.retryable


def _leased_archive(session: Session, owner: User, job_id: str) -> Path:
    """An uploaded ZIP in staging, owned by ``job_id`` the way the route leaves it."""
    import hashlib

    from app.core.config import settings
    from app.modules.ingestion import staging_leases

    staged = settings.incoming_dir / f"{_uuid.uuid4().hex}.zip"
    content = _zip_bytes()
    staged.write_bytes(content)
    staging_leases.create_job_lease(
        session,
        job_id=job_id,
        owner_user_id=owner.id,
        path=staged,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        check_capacity=False,
    )
    session.commit()
    return staged


def _cube_stl_bytes() -> bytes:
    return (
        b"solid cube\nfacet normal 0 0 1\nouter loop\n"
        b"endloop\nendfacet\nendsolid cube\n"
    )


def _zip_bytes(*, entry: str = "cube.stl", content: bytes | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as bundle:
        bundle.writestr(entry, content or _cube_stl_bytes())
    return buf.getvalue()


class TestBatchWithdrawal:
    @pytest.mark.asyncio
    async def test_stops_before_resolving_the_next_member(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.db.models import IngestRequestKind
        from app.modules.work import service
        from tests.factories.content import gcode

        owner = make_user("member-withdrawal")
        request = make_ingest_request(owner, kind=IngestRequestKind.COLLECTION)
        context = build_job_context(request.job_id)
        staged = tmp_path / "first.gcode"
        staged.write_bytes(gcode(marker="first-member"))
        resolved = []
        downloaded = []

        async def resolve(url):
            resolved.append(url)
            return url + "/download"

        async def download(url, *, window_max_bytes):
            assert window_max_bytes > 0
            downloaded.append(url)
            service.cancel(request.job_id, actor=owner)
            return staged, staged.name

        monkeypatch.setattr(import_resolvers, "resolve_page_url", resolve)
        monkeypatch.setattr(importer, "download_to_staging", download)
        members = [
            import_resolvers.CollectionMember(
                page_url="https://example.com/first", title="First", source_id="first"
            ),
            import_resolvers.CollectionMember(
                page_url="https://example.com/second",
                title="Second",
                source_id="second",
            ),
        ]
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            await ingest_background.run_collection_member_import(
                job_context=context,
                members=members,
                target_collection="Selected",
                tags=None,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        assert resolved == [members[0].page_url]
        assert downloaded == [members[0].page_url + "/download"]
        assert not staged.exists()

    @pytest.mark.asyncio
    async def test_stops_before_downloading_the_next_selection(
        self, make_user, make_ingest_request, tmp_path, monkeypatch
    ) -> None:
        from app.core.cancellation import OperationCancelled, cancellation_scope
        from app.modules.work import service
        from tests.factories.content import gcode

        owner = make_user("selection-withdrawal")
        request = make_ingest_request(owner)
        context = build_job_context(request.job_id)
        staged = tmp_path / "first.gcode"
        staged.write_bytes(gcode(marker="first-selection"))
        links = ["https://example.com/first.gcode", "https://example.com/second.gcode"]
        downloaded = []

        files = [
            import_resolvers.ModelFile("first", "first.gcode", "gcode"),
            import_resolvers.ModelFile("second", "second.gcode", "gcode"),
        ]

        async def resolve(page_url, selected):
            assert selected == files
            return (
                import_resolvers.SelectedFileDownload(files[0], links[0]),
                import_resolvers.SelectedFileDownload(files[1], links[1]),
            )

        async def download(url, *, window_max_bytes):
            assert window_max_bytes > 0
            downloaded.append(url)
            service.cancel(request.job_id, actor=owner)
            return staged, staged.name

        monkeypatch.setattr(import_resolvers, "resolve_selected_sources", resolve)
        monkeypatch.setattr(importer, "download_to_staging", download)
        with cancellation_scope(context.cancelled), pytest.raises(OperationCancelled):
            await ingest_background.run_file_selection_import(
                job_context=context,
                page_url="https://example.com/model",
                files=files,
                collection=None,
                tags=None,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        assert downloaded == [links[0]]
        assert not staged.exists()


@pytest.fixture
def batch_window_observation(make_user, make_ingest_request, tmp_path, monkeypatch):
    """Observe real ZIP reads, disposable paths and the first Artifact commit."""
    from app.core.cancellation import cancellation_scope
    from app.core.config import settings
    from app.db.models import File, IngestRequestKind
    from app.modules.ingestion import staging_leases
    from app.modules.storage.storage_backend.runtime import get_backend
    from tests.factories.content import gcode, oversized_gcode, zip_bytes

    monkeypatch.setitem(_overlay, "ingestion_batch_max_files", 1)
    monkeypatch.setitem(_overlay, "ingestion_batch_max_mb", 1)
    owner = make_user("batch-window-owner")
    request = make_ingest_request(owner, kind=IngestRequestKind.ARCHIVE_SELECTION)
    bodies = {
        name: oversized_gcode(min_bytes=700 * 1024) + gcode(marker=name)
        for name in ["first.gcode", "second.gcode", "third.gcode"]
    }
    source_bytes = zip_bytes(bodies, compress=False)
    source = tmp_path / "leased-window.zip"
    source.write_bytes(source_bytes)
    with get_session_factory().scoped_session() as session:
        staging_leases.create_job_lease(
            session,
            job_id=request.job_id,
            owner_user_id=owner.id,
            path=source,
            size_bytes=len(source_bytes),
            sha256=hashlib.sha256(source_bytes).hexdigest(),
        )
        session.commit()
        baseline_files = {
            file.id: file.model_dump() for file in session.exec(select(File)).all()
        }
    context = build_job_context(request.job_id)
    incoming = settings.incoming_dir
    incoming.mkdir(parents=True, exist_ok=True)
    baseline_paths = set(incoming.iterdir())
    seen_entries = []
    observation = {}
    read = zipfile.ZipExtFile.read
    ingest = importer._ingest_one_file

    def observe_read(stream, size=-1):
        if stream.name not in seen_entries:
            seen_entries.append(stream.name)
        return read(stream, size)

    def observe_commit(*args, **kwargs):
        result = ingest(*args, **kwargs)
        if not observation and result is not None and "file_id" in result:
            # Include unfinished private copies; exclude only paths present
            # before this batch and the separately leased ZIP input.
            disposable = set(incoming.iterdir()) - baseline_paths - {source}
            with get_session_factory().scoped_session() as session:
                file = session.get(File, result["file_id"])
                assert file is not None
                observation.update(
                    entries=tuple(seen_entries),
                    count=len(disposable),
                    bytes=sum(path.stat().st_size for path in disposable),
                    first_bytes=get_backend().read_bytes(file.path),
                )
        return result

    monkeypatch.setattr(zipfile.ZipExtFile, "read", observe_read)
    monkeypatch.setattr(importer, "_ingest_one_file", observe_commit)
    with cancellation_scope(context.cancelled):
        ingest_background.run_archive_selection(
            job_context=context,
            archive=source,
            archive_name=source.name,
            names=list(bodies),
            collection=None,
            tags=None,
            source_url=None,
            actor_user_id=owner.id,
            session_factory=get_session_factory(),
        )
    assert observation, "no Artifact was committed"
    assert source.read_bytes() == source_bytes
    with get_session_factory().scoped_session() as session:
        all_files = session.exec(select(File)).all()
        assert {
            file.id: file.model_dump()
            for file in all_files
            if file.id in baseline_files
        } == baseline_files
        assert len([file for file in all_files if file.id not in baseline_files]) == 3
        assert len(staging_leases.job_leases(session, request.job_id)) == 1
    assert set(incoming.iterdir()) == baseline_paths
    observation["expected_first_bytes"] = bodies["first.gcode"]
    return observation


class TestBatchWindows:
    def test_publishes_first_artifact_before_reading_later_entries(
        self, batch_window_observation
    ):
        assert (
            batch_window_observation["first_bytes"]
            == batch_window_observation["expected_first_bytes"]
        )
        assert batch_window_observation["entries"] == ("first.gcode",)

    def test_bounds_disposable_staging_count(self, batch_window_observation):
        assert batch_window_observation["count"] <= 1

    def test_bounds_disposable_staging_bytes(self, batch_window_observation):
        assert batch_window_observation["bytes"] <= 1024 * 1024

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "collection", [False, True], ids=["url-selection", "collection"]
    )
    async def test_publishes_before_downloading_the_next_source(
        self, make_user, make_ingest_request, monkeypatch, collection
    ) -> None:
        import httpx

        from app.core.cancellation import cancellation_scope
        from app.core.url_safety import PinnedTarget
        from app.db.models import File, IngestRequestKind
        from app.modules.storage.storage_backend.runtime import get_backend
        from tests.factories.content import gcode

        monkeypatch.setitem(_overlay, "ingestion_batch_max_files", 1)
        monkeypatch.setitem(_overlay, "ingestion_batch_max_mb", 1)
        owner = make_user("streamed-batch-owner")
        request = make_ingest_request(
            owner,
            kind=IngestRequestKind.COLLECTION
            if collection
            else IngestRequestKind.URL_SELECTION,
        )
        context = build_job_context(request.job_id)
        bodies = {
            "first.gcode": gcode(marker="first-stream"),
            "second.gcode": gcode(marker="second-stream"),
        }
        urls = [f"https://example.com/{name}" for name in bodies]
        with get_session_factory().scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
        observations = []

        class Content(httpx.AsyncByteStream):
            def __init__(self, body):
                self.body = body

            async def __aiter__(self):
                yield self.body

        def serve(outbound):
            name = outbound.url.path.rsplit("/", 1)[1]
            if name == "second.gcode":
                with get_session_factory().scoped_session() as session:
                    new_files = [
                        file
                        for file in session.exec(select(File)).all()
                        if file.id not in baseline
                    ]
                    observations.append(
                        [get_backend().read_bytes(file.path) for file in new_files]
                    )
            return httpx.Response(200, stream=Content(bodies[name]))

        async def selected_links(page_url, files):
            return tuple(
                import_resolvers.SelectedFileDownload(file, url)
                for file, url in zip(files, urls, strict=True)
            )

        async def member_link(page_url):
            return page_url

        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda url: PinnedTarget(url, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer, "pinned_transport", lambda _: httpx.MockTransport(serve)
        )
        monkeypatch.setattr(
            import_resolvers, "resolve_selected_sources", selected_links
        )
        monkeypatch.setattr(import_resolvers, "resolve_page_url", member_link)
        with cancellation_scope(context.cancelled):
            if collection:
                await ingest_background.run_collection_member_import(
                    job_context=context,
                    members=[
                        import_resolvers.CollectionMember(
                            page_url=url, title=name, source_id=name
                        )
                        for url, name in zip(urls, bodies, strict=True)
                    ],
                    target_collection="Streamed batch",
                    tags=None,
                    actor_user_id=owner.id,
                    session_factory=get_session_factory(),
                )
            else:
                await ingest_background.run_file_selection_import(
                    job_context=context,
                    page_url="https://example.com/model",
                    files=[
                        import_resolvers.ModelFile(name, name, "gcode")
                        for name in bodies
                    ],
                    collection=None,
                    tags=None,
                    actor_user_id=owner.id,
                    session_factory=get_session_factory(),
                )
        assert observations == [[bodies["first.gcode"]]]
        status = jobs.get(request.job_id)
        assert status is not None and status.state == "completed"
        assert status.processed == 2
        with get_session_factory().scoped_session() as session:
            all_files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in all_files if file.id in baseline
            } == baseline
            new_files = [file for file in all_files if file.id not in baseline]
            assert len(new_files) == 2
            assert {get_backend().read_bytes(file.path) for file in new_files} == set(
                bodies.values()
            )

    @pytest.mark.parametrize("extra", [0, 1], ids=["exact-cap", "one-byte-over"])
    def test_enforces_the_entry_byte_limit(
        self, make_user, make_ingest_request, tmp_path, monkeypatch, extra
    ) -> None:
        from app.core.cancellation import cancellation_scope
        from app.core.config import settings
        from app.db.models import File, IngestRequestKind
        from app.modules.ingestion import staging_leases
        from app.modules.storage.storage_backend.runtime import get_backend
        from tests.factories.content import oversized_gcode, zip_bytes

        monkeypatch.setitem(_overlay, "ingestion_batch_max_files", 1)
        monkeypatch.setitem(_overlay, "ingestion_batch_max_mb", 1)
        owner = make_user("byte-limit-owner")
        request = make_ingest_request(owner, kind=IngestRequestKind.ARCHIVE_SELECTION)
        body = oversized_gcode(min_bytes=1024 * 1024 + 64)[: 1024 * 1024 + extra]
        source_bytes = zip_bytes({"source.gcode": body}, compress=False)
        source = tmp_path / "limit.zip"
        source.write_bytes(source_bytes)
        with get_session_factory().scoped_session() as session:
            baseline = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
            staging_leases.create_job_lease(
                session,
                job_id=request.job_id,
                owner_user_id=owner.id,
                path=source,
                size_bytes=len(source_bytes),
                sha256=hashlib.sha256(source_bytes).hexdigest(),
            )
            session.commit()
        incoming = settings.incoming_dir
        incoming.mkdir(parents=True, exist_ok=True)
        initial_paths = set(incoming.iterdir())
        context = build_job_context(request.job_id)
        with cancellation_scope(context.cancelled):
            ingest_background.run_archive_selection(
                job_context=context,
                archive=source,
                archive_name=source.name,
                names=["source.gcode"],
                collection=None,
                tags=None,
                source_url=None,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(request.job_id)
        assert status is not None
        with get_session_factory().scoped_session() as session:
            files = session.exec(select(File)).all()
            assert {
                file.id: file.model_dump() for file in files if file.id in baseline
            } == baseline
            new_files = [file for file in files if file.id not in baseline]
            if extra:
                assert status.state == "failed"
                assert status.error == "batch_entry_too_large"
                assert new_files == []
            else:
                assert status.state == "completed"
                assert len(new_files) == 1
                assert get_backend().read_bytes(new_files[0].path) == body
        assert set(incoming.iterdir()) == initial_paths
        assert source.read_bytes() == source_bytes

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "content_length", [None, "1"], ids=["missing-length", "lying-length"]
    )
    async def test_bounds_unknown_length_download(
        self, db_session, monkeypatch, content_length
    ) -> None:
        import httpx

        from app.core.config import settings
        from app.core.url_safety import PinnedTarget
        from app.db.models import CapacityReservation, File
        from tests.factories.content import oversized_gcode

        body = oversized_gcode(min_bytes=1024 * 1024 + 64)[: 1024 * 1024 + 1]
        closed = []

        class Content(httpx.AsyncByteStream):
            async def __aiter__(self):
                for offset in range(0, len(body), 64 * 1024):
                    yield body[offset : offset + 64 * 1024]

            async def aclose(self):
                closed.append(True)

        url = "https://example.com/unknown.gcode"
        headers = {} if content_length is None else {"Content-Length": content_length}
        monkeypatch.setattr(
            importer,
            "_resolve_or_raise",
            lambda _: PinnedTarget(url, "example.com", 443, "93.184.216.34"),
        )
        monkeypatch.setattr(
            importer,
            "pinned_transport",
            lambda _: httpx.MockTransport(
                lambda request: httpx.Response(200, headers=headers, stream=Content()),
            ),
        )
        incoming = settings.incoming_dir
        incoming.mkdir(parents=True, exist_ok=True)
        initial_paths = set(incoming.iterdir())
        with get_session_factory().scoped_session() as session:
            baseline_files = {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            }
        with pytest.raises(importer.ImportError_, match="^batch_entry_too_large$"):
            await importer.download_to_staging(url, window_max_bytes=1024 * 1024)
        assert closed
        assert set(incoming.iterdir()) == initial_paths
        with get_session_factory().scoped_session() as session:
            assert session.exec(select(CapacityReservation)).all() == []
            assert {
                file.id: file.model_dump() for file in session.exec(select(File)).all()
            } == baseline_files
