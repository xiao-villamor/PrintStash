"""Defends artifact persistence requires persisted model at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._ingestion_atomicity_shared import (
    File,
    FileType,
    Metadata,
    Model,
    Path,
    Session,
    _persist,
    _session_factory,
    _staged,
    ingestion,
    pytest,
    registry,
    select,
)


def test_artifact_persistence_requires_persisted_model(
    db_session: Session, storage, tmp_path: Path
) -> None:
    transient = Model(name="Transient", slug="transient", hash="t" * 64)
    staged = _staged(tmp_path)
    before_ids = [row.id for row in db_session.exec(select(File)).all()]

    with pytest.raises(RuntimeError, match="artifact_model_not_persisted"):
        _persist(db_session, transient, staged)

    assert staged.exists()
    assert [row.id for row in db_session.exec(select(File)).all()] == before_ids


def test_version_reservation_rejects_missing_model(db_session: Session) -> None:
    before_ids = [row.id for row in db_session.exec(select(File)).all()]

    with pytest.raises(RuntimeError, match="artifact_model_not_found"):
        ingestion._reserve_next_version(db_session, 999_999)

    db_session.rollback()
    assert [row.id for row in db_session.exec(select(File)).all()] == before_ids


def test_ingestion_key_replay_returns_existing_artifact(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    first = _persist(
        db_session,
        model,
        _staged(tmp_path, "first.stl"),
        ingestion_key="durable-job-1",
    )
    replay_staged = _staged(tmp_path, "replay.stl")

    replay = _persist(
        db_session,
        model,
        replay_staged,
        ingestion_key="durable-job-1",
    )

    assert replay.id == first.id
    assert replay_staged.exists()
    assert db_session.exec(select(File).where(File.model_id == model.id)).all() == [
        first
    ]
    assert len(db_session.exec(select(Metadata)).all()) == 1


def test_content_hash_dedupe_revives_trashed_model(db_session: Session) -> None:
    model = Model(
        name="Recoverable",
        slug="recoverable",
        hash="r" * 64,
        deleted_at=ingestion.utcnow(),
        deleted_by=12,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)

    resolved, created = ingestion.resolve_or_create_model(
        db_session,
        dedup_hash=model.hash,
        model_name="Ignored replacement",
    )

    assert created is False
    assert resolved.id == model.id
    assert resolved.deleted_at is None
    assert resolved.deleted_by is None
    assert (
        len(db_session.exec(select(Model).where(Model.hash == model.hash)).all()) == 1
    )


def test_pipeline_strategy_failure_reports_failed_without_publishing_artifact(
    db_session: Session, storage, tmp_path: Path
) -> None:
    staged = _staged(tmp_path, "parser-failure.stl")
    job_id = registry.create()
    before_model_ids = [row.id for row in db_session.exec(select(Model)).all()]
    before_file_ids = [row.id for row in db_session.exec(select(File)).all()]

    def fail_parse(_path: Path, _report) -> tuple[dict[str, object], bytes | None]:
        raise ValueError("parser rejected malformed mesh")

    strategy = ingestion.IngestionStrategy(FileType.STL, True, fail_parse, ("parse",))

    ingestion.run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged,
        original_filename=staged.name,
        model_name="Malformed",
        collection=None,
        tags=None,
        source_hash=None,
        strategy=strategy,
        session_factory=_session_factory(db_session),
    )

    job = registry.get(job_id)
    assert job is not None
    assert job.state == "failed"
    assert job.error == "parser rejected malformed mesh"
    db_session.expire_all()
    assert [row.id for row in db_session.exec(select(Model)).all()] == before_model_ids
    assert [row.id for row in db_session.exec(select(File)).all()] == before_file_ids
