"""Adopters and collectors arbitrate using actual SQLite/PostgreSQL locks."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from threading import Event, get_ident

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.time import utcnow
from app.db.models import (
    File,
    OwnedStorageObject,
    StorageDeleteIntent,
    StorageObjectState,
)
from app.db.session import SQLiteSessionFactory, _set_sqlite_pragmas
from app.db.url import normalize_database_url
from app.modules.storage import storage_deletion, storage_ownership
from app.modules.storage.storage_backend.runtime import get_backend
from app.modules.storage.storage_deletion import (
    enqueue_prevalidated_receipt,
    process_storage_delete_intents,
)
from app.modules.storage.storage_ownership import (
    adopt_publication,
    prepare_bytes,
    record_creation,
    sweep_orphaned_publications,
)
from tests.containers import fresh_postgres_database
from tests.factories import build_file, build_model, build_system_config


@pytest.fixture(
    params=[
        pytest.param("sqlite", id="sqlite"),
        pytest.param("postgresql", marks=pytest.mark.postgres, id="postgresql"),
    ]
)
def storage_publication_engine(request, tmp_path, monkeypatch):
    if request.param == "sqlite":
        engine = create_engine(
            f"sqlite:///{tmp_path / 'publication.sqlite'}",
            connect_args={"check_same_thread": False},
        )
        event.listen(engine, "connect", _set_sqlite_pragmas)
    else:
        engine = create_engine(
            normalize_database_url(fresh_postgres_database("storage_publication"))
        )
    SQLModel.metadata.create_all(engine)
    factory = SQLiteSessionFactory(engine)
    monkeypatch.setattr(storage_deletion, "get_session_factory", lambda: factory)
    with Session(engine) as session:
        build_system_config(session)
    try:
        yield engine
    finally:
        engine.dispose()


def _prepared(engine, backend):
    with Session(engine) as session:
        file = build_file(session, build_model(session), filename="publication.stl")
        file_id = file.id
        key = backend.thumbnail_key(file_id)
        candidate = prepare_bytes(
            session, backend, key, b"owned", object_kind="thumbnail"
        )
        row = session.get(OwnedStorageObject, candidate.reservation.id)
        assert row is not None
        row.created_at = utcnow() - timedelta(days=2)
        session.add(row)
        session.commit()
    return file_id, candidate


class TestPublicationTransactions:
    def test_adoption_commit_wins_over_a_waiting_sweep(
        self, storage_publication_engine, monkeypatch
    ):
        engine = storage_publication_engine
        backend = get_backend()
        file_id, candidate = _prepared(engine, backend)
        entered = Event()
        caller = get_ident()
        original = storage_ownership.lock_publication_locator

        def observed_lock(session, **locator):
            if get_ident() != caller:
                entered.set()
            return original(session, **locator)

        monkeypatch.setattr(
            storage_ownership, "lock_publication_locator", observed_lock
        )
        with Session(engine) as writer:
            file = writer.get(File, file_id)
            assert file is not None
            file.thumbnail_path = candidate.receipt.key
            writer.add(file)
            writer.flush()
            adopt_publication(writer, candidate)

            def sweep():
                with Session(engine) as collector:
                    return sweep_orphaned_publications(collector, backend)

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(sweep)
                assert entered.wait(10)
                writer.commit()
                result = future.result(timeout=20)

        with Session(engine) as reader:
            file = reader.get(File, file_id)
            assert file is not None
            assert file.thumbnail_path == candidate.receipt.key
            proof = reader.get(OwnedStorageObject, candidate.reservation.id)
            assert proof is not None
            assert proof.state.value == "committed"
            assert not reader.exec(select(StorageDeleteIntent)).all()
        assert result.reclaimed == 0
        assert backend.read_bytes(candidate.receipt.key) == b"owned"

    def test_retirement_commit_rejects_a_waiting_adopter(
        self, storage_publication_engine
    ):
        engine = storage_publication_engine
        backend = get_backend()
        file_id, candidate = _prepared(engine, backend)
        entered = Event()
        with Session(engine) as collector:
            proof = storage_ownership._retire_orphan(collector, candidate.reservation)
            assert proof is not None
            intent = enqueue_prevalidated_receipt(
                collector, candidate.receipt, object_kind="thumbnail", sha256=None
            )
            intent_id = intent.id

            def adopt():
                with Session(engine) as writer:
                    file = writer.get(File, file_id)
                    assert file is not None
                    file.thumbnail_path = candidate.receipt.key
                    writer.add(file)
                    entered.set()
                    with pytest.raises(RuntimeError, match="revoked"):
                        adopt_publication(writer, candidate)
                    writer.rollback()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(adopt)
                assert entered.wait(10)
                collector.commit()
                future.result(timeout=20)

        result = process_storage_delete_intents(backend=backend)
        assert result.completed == 1
        assert not backend.exists(candidate.receipt.key)
        with Session(engine) as reader:
            file = reader.get(File, file_id)
            assert file is not None and file.thumbnail_path is None
            proof = reader.get(OwnedStorageObject, candidate.reservation.id)
            assert proof is not None and proof.state.value == "retiring"
            intent = reader.get(StorageDeleteIntent, intent_id)
            assert intent is not None and intent.status == "completed"

    def test_first_anchor_insert_serializes_an_adopter_without_a_reservation(
        self, storage_publication_engine
    ):
        engine = storage_publication_engine
        backend = get_backend()
        key = backend.thumbnail_key(620)
        receipt = backend.create_bytes(b"legacy", key)
        from dataclasses import replace

        receipt = replace(
            receipt,
            provider_ref=storage_ownership.provider_ref_for_backend(
                backend, namespace=receipt.namespace
            ),
        )
        entered = Event()
        with Session(engine) as collector:
            assert not collector.exec(select(OwnedStorageObject)).all()
            intent = enqueue_prevalidated_receipt(
                collector, receipt, object_kind="thumbnail", sha256=None
            )
            assert intent.id is not None

            def adopt():
                with Session(engine) as writer:
                    entered.set()
                    with pytest.raises(RuntimeError, match="revoked"):
                        record_creation(writer, receipt, object_kind="thumbnail")
                    writer.rollback()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(adopt)
                assert entered.wait(10)
                collector.commit()
                future.result(timeout=20)

        with Session(engine) as reader:
            assert not reader.exec(select(OwnedStorageObject)).all()
        assert backend.read_bytes(key) == b"legacy"
        assert process_storage_delete_intents(backend=backend).completed == 1
        assert not backend.exists(key)

    @pytest.mark.postgres
    @pytest.mark.parametrize(
        "storage_publication_engine", ["postgresql"], indirect=True
    )
    def test_thumbnail_claims_job_before_holding_its_publication_anchor(
        self, storage_publication_engine, monkeypatch
    ):
        from sqlalchemy import text

        from app.db.models import (
            DerivativeKind,
            DerivativeState,
            Job,
            JobKind,
            JobState,
        )
        from app.modules.derivatives import producers, records
        from app.modules.derivatives.kinds import recipes_for
        from app.modules.storage.storage_publication import lock_publication_locator
        from app.modules.work.contracts import JobExecution
        from tests.factories import build_job, content

        engine = storage_publication_engine
        with Session(engine) as setup:
            file = build_file(setup, build_model(setup), filename="job-order.stl")
            job = build_job(
                setup,
                kind=JobKind.DERIVATIVES_MESH,
                state=JobState.RUNNING,
                subject=f"file/{file.id}",
                attempts=1,
            )
            execution = JobExecution(job.id, job.attempts, job.execution_epoch)
            job_id = job.id
            recipe = recipes_for(file)[DerivativeKind.THUMBNAIL]
            row = records.begin(
                setup, file, DerivativeKind.THUMBNAIL, recipe, now=utcnow()
            )
            attempt = records.attempt(setup, file, row, execution=execution)
            setup.commit()
            setup.refresh(file)
            setup.expunge(file)
        factory = SQLiteSessionFactory(engine)
        monkeypatch.setattr(producers, "get_session_factory", lambda: factory)
        backend = get_backend()
        monkeypatch.setattr(producers, "get_backend", lambda: backend)
        at_domain_claim = Event()
        keys = []
        original = records.mark_ready

        def observed_claim(session, captured, **output):
            keys.append(output["storage_key"])
            at_domain_claim.set()
            return original(session, captured, **output)

        monkeypatch.setattr(records, "mark_ready", observed_claim)
        with Session(engine) as authority:
            authority.execute(text("SET LOCAL lock_timeout = '2000ms'"))
            authority.exec(select(Job).where(Job.id == job_id).with_for_update()).one()

            def publish():
                return producers._publish_thumbnail(
                    file,
                    content.png(),
                    attempt=attempt,
                    normalize=False,
                    strategy="embedded",
                    complete=True,
                )

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(publish)
                try:
                    if not at_domain_claim.wait(10):
                        if future.done():
                            future.result()
                        pytest.fail("thumbnail never reached its domain claim")
                    # A writer holding the Job may still acquire the candidate
                    # anchor. Acquiring it inside publish before mark_ready
                    # would block here and PostgreSQL would reject the cycle.
                    backend = get_backend()
                    lock_publication_locator(
                        authority,
                        backend=backend.backend_name,
                        namespace=backend.namespace_for(keys[0]),
                        key=keys[0],
                    )
                    authority.commit()
                finally:
                    authority.rollback()
                assert future.result(timeout=20) is DerivativeState.READY

        with Session(engine) as reader:
            current = reader.get(File, file.id)
            assert current is not None and current.thumbnail_path == keys[0]
            proof = reader.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == keys[0])
            ).one()
            assert proof.state.value == "committed"


class TestFirstReservation:
    def test_concurrent_first_reservations_have_one_current_winner(
        self, storage_publication_engine
    ):
        from app.modules.storage.storage_backend.contracts import StorageCollisionError
        from app.modules.storage.storage_ownership import (
            provider_ref_for_backend,
            reserve_publication,
        )

        backend = get_backend()
        key = backend.thumbnail_key(850)
        namespace = backend.namespace_for(key)
        ref = provider_ref_for_backend(backend, namespace=namespace)
        started = Event()
        with Session(storage_publication_engine) as first:
            winner = reserve_publication(
                first,
                backend=backend.backend_name,
                namespace=namespace,
                key=key,
                provider_ref=ref,
                object_kind="thumbnail",
            )

            def reserve():
                with Session(storage_publication_engine) as contender:
                    started.set()
                    with pytest.raises(StorageCollisionError):
                        reserve_publication(
                            contender,
                            backend=backend.backend_name,
                            namespace=namespace,
                            key=key,
                            provider_ref=ref,
                            object_kind="thumbnail",
                        )
                    contender.rollback()

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(reserve)
                assert started.wait(10)
                first.commit()
                future.result(timeout=10)
        with Session(storage_publication_engine) as check:
            proofs = check.exec(
                select(OwnedStorageObject).where(OwnedStorageObject.key == key)
            ).all()
            assert [
                (row.id, row.publication_generation, row.state) for row in proofs
            ] == [(winner.id, winner.generation, StorageObjectState.PENDING)]


class TestVersionedBackupRetirement:
    @pytest.mark.parametrize(
        "outbox_first", [True, False], ids=["retire-first", "new-generation-first"]
    )
    def test_retired_s3_receipt_cannot_delete_the_new_generation(
        self, storage_publication_engine, monkeypatch, outbox_first
    ):
        from types import SimpleNamespace

        from app.modules.backups.backup import targets
        from app.modules.storage.storage_backend.contracts import CreationReceipt

        class Store:
            versions = {"old": b"same", "new": b"same"}

            def delete_object(self, **kwargs):
                assert kwargs["VersionId"] == "old"
                del self.versions["old"]

        store = Store()
        monkeypatch.setattr(
            targets,
            "_get_backup_s3_target",
            lambda: SimpleNamespace(
                client=store, bucket="bucket", provider_ref="profile"
            ),
        )
        old = CreationReceipt(
            key="printstash-backups/race.zip",
            size=4,
            token="old-token",
            backend="backup-s3",
            namespace="bucket/printstash-backups",
            etag="same-etag",
            version_id="old",
            provider_ref="profile",
        )
        new = CreationReceipt(
            key=old.key,
            size=4,
            token="new-token",
            backend=old.backend,
            namespace=old.namespace,
            etag=old.etag,
            version_id="new",
            provider_ref="profile",
        )
        with Session(storage_publication_engine) as session:
            record_creation(session, old, object_kind="backup")
            session.commit()
            if outbox_first:
                enqueue_prevalidated_receipt(
                    session, old, object_kind="backup", sha256=None
                )
                session.commit()
            proof = record_creation(session, new, object_kind="backup")
            new_id = proof.id
            session.commit()
            if not outbox_first:
                enqueue_prevalidated_receipt(
                    session, old, object_kind="backup", sha256=None
                )
                session.commit()
            with pytest.raises(RuntimeError, match="revoked"):
                record_creation(session, old, object_kind="backup")
            session.rollback()
        assert process_storage_delete_intents().completed == 1
        assert store.versions == {"new": b"same"}
        with Session(storage_publication_engine) as session:
            assert (
                session.get(OwnedStorageObject, new_id).state
                is StorageObjectState.COMMITTED
            )
            assert session.exec(select(StorageDeleteIntent)).one().status == "completed"

    def test_unversioned_outbox_is_retained_without_repeated_delete_attempts(
        self, storage_publication_engine, monkeypatch
    ):
        from types import SimpleNamespace

        from app.modules.backups.backup import targets
        from app.modules.storage.storage_backend.contracts import CreationReceipt

        class Store:
            def delete_object(self, **kwargs):
                pytest.fail("unversioned receipt cannot authorize deletion")

        monkeypatch.setattr(
            targets,
            "_get_backup_s3_target",
            lambda: SimpleNamespace(
                client=Store(), bucket="bucket", provider_ref="profile"
            ),
        )
        receipt = CreationReceipt(
            key="printstash-backups/retained.zip",
            size=4,
            token="old-token",
            backend="backup-s3",
            namespace="bucket/printstash-backups",
            etag="same-etag",
            provider_ref="profile",
        )
        with Session(storage_publication_engine) as session:
            enqueue_prevalidated_receipt(
                session, receipt, object_kind="backup", sha256=None
            )
            session.commit()
        assert process_storage_delete_intents().blocked == 1
        assert process_storage_delete_intents().blocked == 0
        with Session(storage_publication_engine) as session:
            intent = session.exec(select(StorageDeleteIntent)).one()
            assert intent.status == "blocked"
            assert intent.last_error == "storage_reclaim_unsupported"


class TestSourceCoverTransactions:
    @pytest.mark.parametrize(
        "winner_index", [0, 1], ids=["first-candidate", "second-candidate"]
    )
    def test_source_lock_serializes_two_prepared_cover_pointers(
        self, storage_publication_engine, monkeypatch, winner_index
    ):
        import io

        from PIL import Image

        from app.db.models import ModelSourceCover
        from app.modules.library import source_covers
        from tests.factories import build_provenance_source

        engine = storage_publication_engine
        backend = get_backend()

        def png(color):
            out = io.BytesIO()
            Image.new("RGB", (8, 8), color).save(out, format="PNG")
            return out.getvalue()

        with Session(engine) as preparation:
            source = build_provenance_source(preparation, build_model(preparation))
            source_id = source.id
            candidates = [
                source_covers.prepare_candidate(
                    preparation,
                    backend,
                    provenance_source_id=source_id,
                    actor_id=None,
                    data=png(color),
                    content_type="image/png",
                )
                for color in ("red", "navy")
            ]
        entered = Event()
        caller = get_ident()
        original_begin = source_covers.begin_write

        def observed_begin(session, **kwargs):
            if get_ident() != caller:
                entered.set()
            original_begin(session, **kwargs)

        monkeypatch.setattr(source_covers, "begin_write", observed_begin)
        winner, rejected = candidates[winner_index], candidates[1 - winner_index]
        with Session(engine) as writer:
            source_covers.attach_candidate(writer, winner)
            source_covers.adopt_candidate(writer, winner)

            def contender():
                with Session(engine) as other:
                    try:
                        source_covers.attach_candidate(other, rejected)
                    except source_covers.SourceCoverChangedError as exc:
                        other.rollback()
                        return str(exc)
                    pytest.fail("second pointer unexpectedly attached")

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(contender)
                assert entered.wait(10)
                writer.commit()
                assert future.result(timeout=20) == "source_cover_changed"
        with Session(engine) as reader:
            cover = reader.exec(
                select(ModelSourceCover).where(
                    ModelSourceCover.provenance_source_id == source_id
                )
            ).one()
            assert cover.storage_key == winner.publication.receipt.key
            assert (
                reader.get(OwnedStorageObject, winner.publication.reservation.id).state
                is StorageObjectState.COMMITTED
            )
            assert (
                reader.get(
                    OwnedStorageObject, rejected.publication.reservation.id
                ).state
                is StorageObjectState.PENDING
            )
            assert reader.exec(select(StorageDeleteIntent)).all() == []
        from app.modules.media.source_cover_processing import (
            process_source_cover_upload,
        )

        assert (
            backend.read_bytes(winner.publication.receipt.key)
            == process_source_cover_upload(
                png(("red", "navy")[winner_index]), "image/png"
            ).data
        )
        assert (
            backend.read_bytes(rejected.publication.receipt.key)
            == process_source_cover_upload(
                png(("red", "navy")[1 - winner_index]), "image/png"
            ).data
        )

    def test_old_absence_probe_yields_to_committed_cover_adoption(
        self, storage_publication_engine, monkeypatch
    ):
        import io

        from PIL import Image

        from app.db.models import ModelSourceCover, StagingLease
        from app.modules.library import source_covers
        from tests.factories import build_provenance_source

        engine = storage_publication_engine
        backend = get_backend()
        image = io.BytesIO()
        Image.new("RGB", (8, 8), "red").save(image, format="PNG")
        with Session(engine) as preparation:
            source = build_provenance_source(preparation, build_model(preparation))
            source_id = source.id
            prepared = source_covers.prepare_put(
                preparation,
                backend,
                provenance_source_id=source_id,
                actor_id=None,
                data=image.getvalue(),
                content_type="image/png",
            )
            preparation.rollback()
        published = backend.read_bytes(prepared.publication.receipt.key)

        def adoption_wins(_key):
            with Session(engine) as adopter:
                adopt_publication(adopter, prepared.publication)
                adopter.commit()
            return None

        monkeypatch.setattr(backend, "object_info", adoption_wins)
        with Session(engine) as collector:
            cover = collector.exec(
                select(ModelSourceCover).where(
                    ModelSourceCover.provenance_source_id == source_id
                )
            ).one()
            lease = collector.exec(
                select(StagingLease).where(
                    StagingLease.model_source_cover_id == cover.id
                )
            ).one()
            assert not source_covers._discard_cover_if_absent(
                collector, backend, cover=cover, lease=lease
            )
            collector.commit()
        with Session(engine) as reader:
            assert (
                reader.exec(select(ModelSourceCover.storage_key)).one()
                == prepared.publication.receipt.key
            )
            assert (
                reader.get(
                    OwnedStorageObject, prepared.publication.reservation.id
                ).state
                is StorageObjectState.COMMITTED
            )
            assert reader.exec(select(StorageDeleteIntent)).all() == []
        assert backend.read_bytes(prepared.publication.receipt.key) == published


class TestPublicationReservation:
    def test_captures_immutable_reservation_identity(self, db_session):
        from app.modules.storage.storage_publication import PublicationReservation
        from tests.factories import build_owned_storage_object

        row = build_owned_storage_object(db_session, provider_ref="saved-provider")

        reservation = PublicationReservation.of(row)
        assert (
            reservation.id,
            reservation.generation,
            reservation.backend,
            reservation.namespace,
            reservation.key,
            reservation.provider_ref,
        ) == (
            row.id,
            row.publication_generation,
            row.backend,
            row.namespace,
            row.key,
            "saved-provider",
        )
        row.publication_generation = "replacement-generation"
        assert reservation.generation != row.publication_generation

    @pytest.mark.parametrize(
        "changed",
        [
            pytest.param({"id": None}, id="missing-id"),
            pytest.param({"publication_generation": ""}, id="missing-generation"),
        ],
    )
    def test_refuses_missing_reservation_identity(self, db_session, changed):
        from app.modules.storage.storage_publication import PublicationReservation
        from tests.factories import build_owned_storage_object

        row = build_owned_storage_object(db_session)
        invalid = row.model_copy(update=changed)

        with pytest.raises(ValueError, match="storage_reservation_identity_missing"):
            PublicationReservation.of(invalid)
        assert PublicationReservation.of(row).generation == row.publication_generation


@pytest.fixture
def physical_receipt():
    from app.modules.storage.storage_backend.contracts import CreationReceipt

    return CreationReceipt(
        key="files/physical.stl",
        size=4,
        token="current-token",
        backend="local",
        namespace="local/vault",
        device=10,
        inode=20,
        ctime_ns=30,
    )


class TestSameCreation:
    def test_accepts_historical_local_token_alias(self, physical_receipt):
        from app.modules.storage.storage_publication import same_creation

        historical = replace(
            physical_receipt, token="historical-token", etag="legacy-etag"
        )

        assert same_creation(physical_receipt, historical)
        assert same_creation(historical, physical_receipt)

    @pytest.mark.parametrize(
        "changed",
        [
            pytest.param({"backend": "s3"}, id="backend"),
            pytest.param({"namespace": "local/other"}, id="namespace"),
            pytest.param({"key": "files/other.stl"}, id="key"),
            pytest.param({"size": 5}, id="size"),
            pytest.param({"device": 11}, id="device"),
            pytest.param({"inode": 21}, id="inode"),
            pytest.param({"ctime_ns": 31}, id="ctime"),
        ],
    )
    def test_refuses_a_different_local_physical_generation(
        self, physical_receipt, changed
    ):
        from app.modules.storage.storage_publication import same_creation

        replacement = replace(physical_receipt, **changed)

        assert not same_creation(physical_receipt, replacement)
        assert not same_creation(replacement, physical_receipt)

    def test_accepts_remote_version_with_historical_logical_token(
        self, physical_receipt
    ):
        from app.modules.storage.storage_publication import same_creation

        current = replace(
            physical_receipt,
            backend="s3",
            version_id="immutable-version",
            device=None,
            inode=None,
            ctime_ns=None,
        )
        historical = replace(current, token="legacy-token", etag="legacy-etag")

        assert same_creation(current, historical)
        assert same_creation(historical, current)

    def test_refuses_same_etag_with_another_remote_version(self, physical_receipt):
        from app.modules.storage.storage_publication import same_creation

        current = replace(
            physical_receipt,
            backend="s3",
            version_id="captured",
            etag="same-etag",
            device=None,
            inode=None,
            ctime_ns=None,
        )
        replacement = replace(current, version_id="replacement")

        assert not same_creation(current, replacement)

    @pytest.mark.parametrize(
        "version",
        [None, "", "null"],
        ids=["missing-version", "empty-version", "null-version"],
    )
    def test_accepts_exact_unversioned_remote_receipt(self, physical_receipt, version):
        from app.modules.storage.storage_publication import same_creation

        receipt = replace(
            physical_receipt,
            backend="s3",
            version_id=version,
            device=None,
            inode=None,
            ctime_ns=None,
        )

        assert same_creation(receipt, replace(receipt))

    @pytest.mark.parametrize(
        "version",
        [None, "", "null"],
        ids=["missing-version", "empty-version", "null-version"],
    )
    def test_refuses_unversioned_remote_token_alias(self, physical_receipt, version):
        from app.modules.storage.storage_publication import same_creation

        receipt = replace(
            physical_receipt,
            backend="s3",
            version_id=version,
            device=None,
            inode=None,
            ctime_ns=None,
        )
        replacement = replace(receipt, token="other-token")

        assert not same_creation(receipt, replacement)

    def test_uses_exact_evidence_when_local_stat_identity_is_missing(
        self, physical_receipt
    ):
        from app.modules.storage.storage_publication import same_creation

        legacy = replace(physical_receipt, device=None, inode=None, ctime_ns=None)
        alias = replace(legacy, token="another-token")

        assert same_creation(legacy, replace(legacy))
        assert not same_creation(legacy, alias)
