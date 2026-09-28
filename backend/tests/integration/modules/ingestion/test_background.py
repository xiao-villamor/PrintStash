"""Import handlers report durable progress and preserve staged review manifests.

Each handler runs as the step of an ``ingest.*`` Job, against the
``IngestRequest`` that Job owns; a review manifest is written back onto that
request, so any process can serve the later selection.
"""

from __future__ import annotations

import io
import json
import uuid as _uuid
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

import app.modules.ingestion.background as ingest_background
from app.db.models import IngestRequest, User
from app.db.session import get_session_factory
from app.modules.ingestion import import_resolvers, importer
from app.modules.ingestion.importer import ImportError_
from app.modules.work.jobs import jobs
from tests._env import use_local_storage
from tests.factories import (
    build_user,
)
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


class TestDownloadAndCollect:
    @pytest.mark.asyncio
    async def test_skips_a_direct_file_that_is_not_importable(
        self,
        monkeypatch,
    ) -> None:
        async def fake_download(url: str):
            staged = Path.cwd() / "readme.txt"
            staged.write_bytes(b"not a model")
            return staged, "readme.txt"

        monkeypatch.setattr(importer, "download_to_staging", fake_download)
        result = await ingest_background._download_and_collect(
            "https://cdn.test/readme.txt"
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_the_entries_of_a_zip(self, monkeypatch) -> None:
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w") as bundle:
            bundle.writestr("cube.stl", _cube_stl_bytes())

        async def fake_download(url: str):
            staged = Path.cwd() / "bundle.zip"
            staged.write_bytes(zip_bytes.getvalue())
            return staged, "bundle.zip"

        monkeypatch.setattr(importer, "download_to_staging", fake_download)
        result = await ingest_background._download_and_collect(
            "https://cdn.test/bundle.zip"
        )
        assert [name for _path, name in result] == ["cube.stl"]

    @pytest.mark.asyncio
    async def test_returns_a_direct_mesh_file_unchanged(self, monkeypatch) -> None:
        async def fake_download(url: str):
            staged = Path.cwd() / "cube.stl"
            staged.write_bytes(_cube_stl_bytes())
            return staged, "cube.stl"

        monkeypatch.setattr(importer, "download_to_staging", fake_download)
        result = await ingest_background._download_and_collect(
            "https://cdn.test/cube.stl"
        )
        assert [name for _path, name in result] == ["cube.stl"]


class TestStageMembers:
    @pytest.mark.asyncio
    async def test_stage_members_isolates_per_member_failures(self) -> None:
        good = import_resolvers.CollectionMember(
            page_url="https://ok.test/model", title="Good", source_id="1"
        )
        bad = import_resolvers.CollectionMember(
            page_url="https://bad.test/model", title="Bad", source_id="2"
        )
        crashy = import_resolvers.CollectionMember(
            page_url="https://crash.test/model", title="Crashy", source_id="3"
        )

        async def fake_resolve(url: str):
            if url == "https://bad.test/model":
                raise importer.ImportError_("member_resolve_failed")
            if url == "https://crash.test/model":
                raise RuntimeError("boom")
            return None  # unresolved -> treat page url itself as a direct link

        async def fake_download_and_collect(url: str):
            return [(Path("cube.stl"), "cube.stl")] if url == good.page_url else []

        with (
            patch.object(
                import_resolvers, "resolve_page_url", side_effect=fake_resolve
            ),
            patch.object(
                ingest_background,
                "_download_and_collect",
                side_effect=fake_download_and_collect,
            ),
        ):
            groups = await ingest_background._stage_members([good, bad, crashy])

        by_title = {g.title: g for g in groups}
        assert by_title["Good"].error is None
        assert by_title["Good"].staged_files == [(Path("cube.stl"), "cube.stl")]
        assert by_title["Bad"].error == "member_resolve_failed"
        assert by_title["Crashy"].error == "boom"

    @pytest.mark.asyncio
    async def test_stage_members_reports_no_importable_files_without_error(
        self,
    ) -> None:
        empty = import_resolvers.CollectionMember(
            page_url="https://empty.test/model", title="Empty", source_id="9"
        )
        with (
            patch.object(
                import_resolvers, "resolve_page_url", AsyncMock(return_value=None)
            ),
            patch.object(
                ingest_background, "_download_and_collect", AsyncMock(return_value=[])
            ),
        ):
            groups = await ingest_background._stage_members([empty])
        assert groups[0].error == "no_importable_files"


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
                job_id=job_id,
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
        self, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        from app.schemas.ingest import UrlIngestRequest

        req = UrlIngestRequest(url="https://printables.com/collections/9", review=False)
        members = [
            import_resolvers.CollectionMember(
                page_url="https://printables.com/model/1", title="A", source_id="1"
            )
        ]
        staged = tmp_path / "staging" / "cube.stl"
        staged.parent.mkdir(parents=True, exist_ok=True)
        staged.write_bytes(_cube_stl_bytes())

        with (
            patch.object(
                import_resolvers,
                "resolve_collection_url",
                AsyncMock(return_value=("Cool Collection", members)),
            ),
            patch.object(
                ingest_background,
                "_stage_members",
                AsyncMock(
                    return_value=[
                        importer.ResolvedGroup(
                            source_url=members[0].page_url,
                            title="A",
                            staged_files=[(staged, "cube.stl")],
                        )
                    ]
                ),
            ),
        ):
            await ingest_background._handle_collection_url(
                job_id=job_id,
                req=req,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "completed"
        assert status.succeeded == 1


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
                job_id=job_id,
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
                job_id=job_id,
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
                job_id=job_id,
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

        async def fake_download(url: str):
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
                job_id=job_id,
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

        async def fake_download(url: str):
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
                job_id=job_id,
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
                job_id=job_id,
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

        async def fake_download(url: str):
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
                job_id=job_id,
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

        async def fake_download(url: str):
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
                job_id=job_id,
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
            job_id=job_id,
            staged=staged,
            original_filename="staged.zip",
            cancelled=lambda: False,
        )

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == ("failed", "no_importable_files")
        assert not staged.exists()

    def test_reports_prepared_file_count_on_the_job(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        ingest_background.inspect_uploaded_archive(
            job_id=job_id,
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
            job_id=job_id,
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
            job_id=job_id, staged=staged, original_filename="staged.zip", cancelled=lambda: False
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
                job_id=job_id, staged=staged, original_filename="staged.zip", cancelled=lambda: False
            )

        status = jobs.get(job_id)
        assert status is not None
        assert (status.state, status.error) == ("failed", "archive_zip_bomb")

    def test_releases_the_staged_archive_it_refused(
        self, db_session: Session, owner: User, tmp_path: Path, job_id: str
    ) -> None:
        use_local_storage(tmp_path)
        staged = _leased_archive(db_session, owner, job_id)

        with patch.object(
            importer, "inspect_archive", side_effect=ImportError_("archive_zip_bomb")
        ):
            ingest_background.inspect_uploaded_archive(
                job_id=job_id, staged=staged, original_filename="staged.zip", cancelled=lambda: False
            )

        assert not staged.exists()

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
                    job_id=job_id, staged=staged, original_filename="staged.zip", cancelled=lambda: False
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
            "resolve_selected_download",
            AsyncMock(side_effect=ImportError_("printables_resolve_failed")),
        ):
            await ingest_background.run_file_selection_import(
                job_id=job_id,
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
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        with (
            patch.object(
                import_resolvers,
                "resolve_selected_download",
                AsyncMock(return_value=["https://cdn.test/readme.txt"]),
            ),
            patch.object(
                ingest_background, "_download_and_collect", AsyncMock(return_value=[])
            ),
        ):
            await ingest_background.run_file_selection_import(
                job_id=job_id,
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
        assert status.error == "no_importable_files"

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
            "resolve_selected_download",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await ingest_background.run_file_selection_import(
                job_id=job_id,
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
        self,
        owner: User,
        tmp_path: Path,
        job_id: str,
    ) -> None:
        use_local_storage(tmp_path)
        with patch.object(
            ingest_background,
            "_stage_members",
            AsyncMock(side_effect=RuntimeError("boom")),
        ):
            await ingest_background.run_collection_member_import(
                job_id=job_id,
                members=[],
                target_collection="Cool",
                tags=None,
                actor_user_id=owner.id,
                session_factory=get_session_factory(),
            )
        status = jobs.get(job_id)
        assert status is not None
        assert status.state == "failed"
        assert status.error == "boom"


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
