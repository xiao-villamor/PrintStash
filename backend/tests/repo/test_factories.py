"""The builders are load-bearing, so their promises are tested like anything else.

Every test in the suite arranges its state through `tests/factories`, which makes
a wrong builder worse than a wrong test: it fails *silently*, by producing a row
that inserts cleanly and is then invisible to the code under test. Every test
built on it then passes against nothing. That failure mode is exactly what a
handful of tests in this repo were doing before this file existed — asserting
against a code path their setup never reached.

So each promise a builder makes to its callers gets a row here:

* a generated identity is unique, and reproducible from the test that asked for it
* a keyword that stands for an encoded state (`trashed`, `recommended`,
  `scanning`, `uploaded`, `expired`) actually produces the state the production
  code looks for
* naming a printer's provider fills in the credentials that provider requires
* a scenario's rows are consistent with each other

The rows below assert through production predicates (`scopes.live()`, the client
factory, the share-link validator) rather than by reading the columns back, since
the column being set is not the promise — being *seen* by the app is.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    Model,
    PrinterProvider,
    PrintJobState,
)
from app.db.scopes import live, trashed
from tests import factories


class TestGeneratedIdentities:
    def test_two_models_never_collide_on_slug_or_hash(
        self, db_session: Session
    ) -> None:
        first = factories.build_model(db_session)
        second = factories.build_model(db_session)

        # Both columns are UNIQUE, so a shared value is an IntegrityError the
        # moment a test builds a second row.
        assert (first.slug, first.hash) != (second.slug, second.hash)

    def test_two_artifacts_never_collide_on_sha256(self, db_session: Session) -> None:
        model = factories.build_model(db_session)

        first = factories.build_file(db_session, model)
        second = factories.build_file(db_session, model)

        assert first.sha256 != second.sha256

    def test_a_generated_value_is_reproducible_within_a_test(
        self, db_session: Session
    ) -> None:
        factories.reset_counters()
        first_run = factories.build_model(db_session).slug
        db_session.delete(db_session.exec(select(Model)).all()[-1])
        db_session.commit()
        factories.reset_counters()

        # The autouse `_reset_factory_counters` fixture does this per test, which
        # is what makes `model-1` in a failure message mean something.
        assert factories.build_model(db_session).slug == first_run


class TestBuildModel:
    def test_is_visible_through_the_live_scope(self, db_session: Session) -> None:
        model = factories.build_model(db_session)

        found = db_session.exec(select(Model).where(live(Model))).all()

        assert model.id in [row.id for row in found]

    def test_trashed_is_hidden_from_the_live_scope(self, db_session: Session) -> None:
        model = factories.build_model(db_session, trashed=True)

        live_ids = [row.id for row in db_session.exec(select(Model).where(live(Model)))]
        trashed_ids = [
            row.id for row in db_session.exec(select(Model).where(trashed(Model)))
        ]

        # `trashed=True` has to mean invisible to every read path, not just a
        # column that happens to be set.
        assert model.id not in live_ids
        assert model.id in trashed_ids

    def test_a_collection_row_becomes_the_foreign_key(
        self, db_session: Session
    ) -> None:
        collection = factories.build_collection(db_session, "Parts")

        model = factories.build_model(db_session, collection=collection)

        assert model.collection_id == collection.id


class TestBuildFile:
    def test_takes_the_models_next_version(self, db_session: Session) -> None:
        model = factories.build_model(db_session)

        first = factories.build_file(db_session, model)
        second = factories.build_file(db_session, model)

        # Two artifacts at the same version is a state the app cannot produce,
        # and it makes every "latest revision" read ambiguous.
        assert (first.version, second.version) == (1, 2)

    def test_recommending_a_revision_demotes_the_previous_one(
        self, db_session: Session
    ) -> None:
        model = factories.build_model(db_session)
        first = factories.build_file(db_session, model, recommended=True)

        factories.build_file(db_session, model, recommended=True)

        db_session.refresh(first)
        # At most one recommended live G-code per model. Without this, a setup
        # with "three revisions, the newest recommended" leaves two.
        assert first.is_recommended is False

    def test_the_dispatchable_builder_matches_what_dispatch_queries_for(
        self, db_session: Session
    ) -> None:
        model = factories.build_model(db_session)

        gcode = factories.build_file(
            db_session,
            model,
            file_type=FileType.GCODE,
            recommended=True,
            status=FileRevisionStatus.KNOWN_GOOD,
        )

        assert (gcode.is_recommended, gcode.revision_status) == (
            True,
            FileRevisionStatus.KNOWN_GOOD,
        )

    def test_trashed_is_hidden_from_the_live_scope(self, db_session: Session) -> None:
        model = factories.build_model(db_session)
        artifact = factories.build_file(db_session, model, trashed=True)

        live_ids = [row.id for row in db_session.exec(select(File).where(live(File)))]

        assert artifact.id not in live_ids

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            pytest.param("part.stl", FileType.STL, id="stl"),
            pytest.param("plate.3mf", FileType.THREE_MF, id="3mf"),
            pytest.param("rev.gcode", FileType.GCODE, id="gcode"),
        ],
    )
    def test_derives_the_type_from_the_filename(
        self, db_session: Session, filename: str, expected: FileType
    ) -> None:
        model = factories.build_model(db_session)

        artifact = factories.build_file(db_session, model, filename=filename)

        # A `GCODE` row called `part.stl` is skipped by every mesh path and
        # picked up by every G-code path, so a test builds what it believes is a
        # mesh and asserts against a list that never contains it. Deriving the
        # type removes the chance for the two to disagree.
        assert artifact.file_type is expected


class TestBuildUser:
    def test_is_not_a_superuser_by_default(self, db_session: Session) -> None:
        # Thirteen hand-rolled `_user` helpers disagreed about this, so the same
        # call meant opposite things depending on the file. A plain user is the
        # interesting case for any access-control row.
        assert factories.build_user(db_session).is_superuser is False

    def test_can_log_in_with_the_default_password(self, client, db_session) -> None:
        user = factories.build_user(db_session, "login-probe")

        response = client.post(
            "/api/v1/auth/login",
            json={"username": user.username, "password": factories.PASSWORD},
        )

        # The password goes through the production hasher, so tests that drive
        # the real login endpoint work without special-casing.
        assert response.status_code == 200, response.text

    def test_bearer_headers_authenticate_that_user(self, client, db_session) -> None:
        user = factories.build_user(db_session, "header-probe", superuser=True)

        response = client.get("/api/v1/auth/me", headers=factories.bearer(user))

        assert response.status_code == 200, response.text
        assert response.json()["username"] == "header-probe"


class TestBuildPrinter:
    @pytest.mark.parametrize(
        "provider",
        list(PrinterProvider),
        ids=[provider.value for provider in PrinterProvider],
    )
    def test_every_provider_builds_a_usable_client(
        self, db_session: Session, provider: PrinterProvider
    ) -> None:
        from app.services.printer_provider import (
            build_provider_registry,
            get_provider_client,
        )

        printer = factories.build_printer(db_session, provider=provider)

        # This is the whole point of the provider-aware defaults: one table holds
        # every provider's credentials, all nullable, so a half-configured row
        # inserts happily and then fails inside a dispatch instead of here.
        client = get_provider_client(printer, registry=build_provider_registry())

        assert client is not None

    def test_a_deliberately_omitted_credential_is_refused(
        self, db_session: Session
    ) -> None:
        from app.services.printer_provider import (
            ProviderError,
            build_provider_registry,
            get_provider_client,
        )

        printer = factories.build_printer(
            db_session,
            provider=PrinterProvider.BAMBU_LAN,
            bambu_access_code=None,
        )

        # Passing a field as `None` is how a test says "misconfigured on purpose",
        # and it has to still reach the production guard.
        with pytest.raises(ProviderError):
            get_provider_client(printer, registry=build_provider_registry())

    def test_is_ready_by_default(self, db_session: Session) -> None:
        # An offline printer is skipped by dispatch, so a test that forgets the
        # status ends up asserting against an empty fleet.
        assert factories.build_printer(db_session).status.value == "ready"


class TestBuildPrintJob:
    def test_derives_its_model_from_the_artifact(self, db_session: Session) -> None:
        model = factories.build_model(db_session)
        gcode = factories.build_file(db_session, model)

        job = factories.build_print_job(db_session, gcode)

        # A job whose model does not own its file is unreachable in production and
        # makes every read path that joins the two return nothing.
        assert job.model_id == model.id


class TestBuildExternalLibrary:
    def test_scanning_holds_a_complete_claim(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        library = factories.build_external_library(db_session, tmp_path, scanning=True)

        # All three parts are checked together by the scan endpoint; setting one
        # by hand is a setup that looks right and does nothing.
        assert library.scan_claim_token is not None
        assert library.scan_claim_expires_at is not None
        assert library.scan_job_id is not None

    def test_is_not_scanning_by_default(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        library = factories.build_external_library(db_session, tmp_path)

        assert library.scan_claim_token is None


class TestBuildShareLink:
    def test_a_fresh_link_is_valid(self, db_session: Session) -> None:
        from app.services import share

        model = factories.build_model(db_session)
        link = factories.build_share_link(db_session, model)

        assert share.is_active(link) is True

    def test_expired_is_rejected_by_the_validator(self, db_session: Session) -> None:
        from app.services import share

        model = factories.build_model(db_session)
        link = factories.build_share_link(db_session, model, expired=True)

        assert share.is_active(link) is False

    def test_revoked_is_rejected_by_the_validator(self, db_session: Session) -> None:
        from app.services import share

        model = factories.build_model(db_session)
        link = factories.build_share_link(db_session, model, revoked=True)

        assert share.is_active(link) is False

    def test_stores_only_the_hash_of_the_token(self, db_session: Session) -> None:
        model = factories.build_model(db_session)

        link = factories.build_share_link(db_session, model)

        assert factories.ops.SHARE_TOKEN not in link.token_hash


class TestCaptureManifests:
    def test_a_manifest_matches_the_provenance_source_it_was_built_from(
        self, db_session: Session
    ) -> None:
        model = factories.build_model(db_session)
        source = factories.build_provenance_source(db_session, model)

        manifest = factories.manifest_for_source(source)

        # The import path matches all three of these against exactly one source.
        # A mismatch makes the match find zero rows and the import refuse without
        # raising, which is a test asserting against a dead path.
        assert manifest["source"]["provider"] == source.provider
        assert manifest["source"]["canonical_url"] == source.canonical_url
        assert manifest["source"]["source_item_id"] == source.source_item_id

    def test_the_default_source_block_carries_all_three_identity_fields(self) -> None:
        source = factories.capture_source()

        assert source["provider"]
        assert source["canonical_url"]
        assert source["source_item_id"]


class TestBuildCaptureSlot:
    def test_uploaded_gives_the_slot_bytes_the_import_path_can_find(
        self, db_session: Session
    ) -> None:
        from app.db.models import CaptureUploadSlotState

        user = factories.build_user(db_session)
        item = factories.build_inbox_item(db_session, user)

        slot = factories.build_capture_slot(db_session, item, uploaded=True)

        # A PENDING slot is skipped by finalize and by the cover attach, so a test
        # that omits this asserts against a no-op.
        assert slot.state is CaptureUploadSlotState.UPLOADED
        assert slot.storage_key is not None


class TestBuildAuditFinding:
    def test_an_open_namespace_escape_blocks_every_purge(
        self, db_session: Session
    ) -> None:
        from app.services import trash
        from app.services.storage_ownership import UnsafeStorageDeleteError

        admin = factories.build_user(db_session, superuser=True)
        run = factories.build_audit_run(db_session, admin)

        factories.build_audit_finding(
            db_session, run, code="managed_storage_namespace_escape"
        )

        # That code plus OPEN is a switch rather than a record: while it stands,
        # no ownership proof is trustworthy and nothing may be deleted.
        with pytest.raises(UnsafeStorageDeleteError):
            trash._require_destructive_maintenance_safe(db_session)

    def test_another_open_finding_does_not(self, db_session: Session) -> None:
        from app.services import trash

        admin = factories.build_user(db_session, superuser=True)
        run = factories.build_audit_run(db_session, admin)

        factories.build_audit_finding(db_session, run, code="orphan_blob")

        trash._require_destructive_maintenance_safe(db_session)


class TestScenarios:
    def test_a_dispatchable_gcode_artifact_is_ready_to_dispatch(
        self, db_session: Session
    ) -> None:
        gcode = factories.a_gcode_artifact(db_session, dispatchable=True)

        # Dispatch only considers a recommended revision and the queue endpoints
        # only accept a known-good one, so both have to hold for the scenario to
        # be the shape its name claims.
        assert gcode.is_recommended is True
        assert gcode.revision_status is FileRevisionStatus.KNOWN_GOOD
        assert gcode.file_type is FileType.GCODE

    def test_a_plain_gcode_artifact_is_neither(self, db_session: Session) -> None:
        gcode = factories.a_gcode_artifact(db_session)

        # The default is the plain artifact, because most tests want one and
        # silently getting a dispatchable one would make a "not dispatched" row
        # pass for the wrong reason.
        assert gcode.is_recommended is False
        assert gcode.revision_status is None

    def test_a_gcode_artifact_hangs_under_its_own_model(
        self, db_session: Session
    ) -> None:
        first = factories.a_gcode_artifact(db_session, "First")
        second = factories.a_gcode_artifact(db_session, "Second")

        assert first.model_id != second.model_id

    def test_a_printer_with_a_queue_has_a_defined_order(
        self, db_session: Session
    ) -> None:
        from app.db.models import PrintJob

        printer, _artifacts = factories.a_printer_with_a_queue(db_session, depth=3)

        jobs = db_session.exec(
            select(PrintJob)
            .where(PrintJob.printer_id == printer.id)
            .order_by(PrintJob.queue_position)
        ).all()

        # Jobs built without a position all sit at 0, where "the next job" is
        # whichever row the database happens to return first.
        assert [job.queue_position for job in jobs] == [0, 1, 2]
        assert all(job.state is PrintJobState.QUEUED for job in jobs)

    def test_the_member_builder_yields_one_model_it_cannot_reach(
        self, db_session: Session
    ) -> None:
        member, allowed, denied = factories.a_member_who_can_see_one_collection(
            db_session
        )

        # Both halves are the point: a test with only the visible model passes
        # identically against a filter that returns everything.
        assert member.is_superuser is False
        assert allowed.collection_id != denied.collection_id


class TestStorageOwnership:
    """The one distinction where a wrong builder is dangerous, not just wrong.

    `build_stored_file` and `build_unowned_file` look interchangeable and are
    opposites: the first may be purged, the second must never be. A test that
    reaches for the wrong one still passes — it just proves the reverse of what
    it claims. So both directions are asserted here, through the real GC.
    """

    def test_a_stored_file_is_purged_with_its_bytes(
        self, db_session: Session, local_storage
    ) -> None:
        from app.services.storage_backend import get_backend
        from app.services.trash import gc_soft_deleted

        backend = get_backend()
        model = factories.build_model(db_session)
        artifact = factories.build_stored_file(db_session, backend, model)
        artifact.deleted_at = utcnow() - timedelta(days=1)
        db_session.add(artifact)
        db_session.commit()
        # Read both off the row *before* the purge: afterwards the instance is
        # gone and touching an attribute raises ObjectDeletedError.
        artifact_id, key = artifact.id, artifact.path

        gc_soft_deleted(retention_days=0)

        db_session.expire_all()
        assert db_session.get(File, artifact_id) is None
        assert not Path(key).exists()

    def test_an_unowned_file_is_refused_with_its_bytes_intact(
        self, db_session: Session, local_storage
    ) -> None:
        from app.services.storage_backend import get_backend
        from app.services.trash import gc_soft_deleted

        backend = get_backend()
        model = factories.build_model(db_session)
        artifact = factories.build_unowned_file(db_session, backend, model)
        artifact.deleted_at = utcnow() - timedelta(days=1)
        db_session.add(artifact)
        db_session.commit()
        artifact_id, key = artifact.id, artifact.path

        result = gc_soft_deleted(retention_days=0)

        # The configured data_dir may be somebody's mounted library, so an
        # unclaimed path is never proof that PrintStash may delete it.
        db_session.expire_all()
        assert result["resources_blocked"] == 1
        assert db_session.get(File, artifact_id) is not None
        assert Path(key).read_bytes() == b"legacy-user-bytes"
