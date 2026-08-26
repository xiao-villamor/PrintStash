"""Defends persists file and metadata together at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._ingestion_atomicity_shared import (
    Engine,
    File,
    FileType,
    Metadata,
    Model,
    ModelProvenanceField,
    Path,
    ProvenanceCapture,
    Session,
    SQLiteSessionFactory,
    SQLModel,
    User,
    _break_durable_row_set,
    _persist,
    _session_factory,
    _set_sqlite_pragmas,
    _staged,
    create_engine,
    event,
    hashlib,
    ingestion,
    provenance,
    pytest,
    registry,
    select,
    threading,
    thumbnail,
)


def test_persists_file_and_metadata_together(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    file_row = _persist(db_session, model, _staged(tmp_path))

    assert file_row.id is not None
    md = db_session.exec(
        select(Metadata).where(Metadata.file_id == file_row.id)
    ).first()
    assert md is not None and md.estimated_time_s == 120


def test_provenance_attachment_shares_artifact_transaction(
    db_session: Session,
    storage,
    model: Model,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provenance failure must roll back the freshly-flushed Artifact too."""

    seen_file_ids: list[int] = []

    def _boom(session: Session, file_row: File, context: object) -> None:
        del session, context
        assert file_row.id is not None
        seen_file_ids.append(file_row.id)
        raise RuntimeError("provenance boom")

    monkeypatch.setattr(ingestion, "_attach_ingested_artifact", _boom)

    with pytest.raises(RuntimeError, match="provenance boom"):
        _persist(
            db_session,
            model,
            _staged(tmp_path),
            provenance_context=object(),
        )

    db_session.rollback()
    assert seen_file_ids
    assert db_session.exec(select(File).where(File.model_id == model.id)).all() == []


def test_deduplicated_pipeline_recapture_refreshes_provenance_without_new_artifact(
    db_session: Session, storage, tmp_path: Path
) -> None:
    """A reusable blob still records a newer source snapshot before terminal dedupe."""
    staged = _staged(tmp_path)
    blob_hash = hashlib.sha256(staged.read_bytes()).hexdigest()
    actor = User(
        username="capture-owner", hashed_password="not-used", is_superuser=True
    )
    model = Model(name="Bracket", slug="bracket", hash=blob_hash)
    db_session.add_all([actor, model])
    db_session.commit()
    db_session.refresh(actor)
    db_session.refresh(model)
    assert model.id is not None
    file_row = File(
        model_id=model.id,
        path="provenance/existing.stl",
        original_filename=staged.name,
        file_type=FileType.STL,
        size_bytes=staged.stat().st_size,
        sha256=blob_hash,
    )
    db_session.add(file_row)
    db_session.flush()

    def manifest(title: str, revision: str):
        return provenance.CaptureManifestV2.from_dict(
            {
                "schema_version": 2,
                "kind": "model_files",
                "source": {
                    "provider": "printables",
                    "canonical_url": "https://printables.com/model/42",
                    "source_item_id": "42",
                    "source_revision": revision,
                    "adapter_version": "test",
                    "tags": [],
                    "fields": {"title": {"value": title, "origin": "confirmed"}},
                },
                "files": [
                    {
                        "id": "42:file",
                        "name": staged.name,
                        "file_type": "stl",
                        "size": staged.stat().st_size,
                    }
                ],
                "selected_ids": ["42:file"],
            }
        )

    first = provenance.ProvenanceContext(
        manifest=manifest("Original", "r1"),
        source_file_id="42:file",
        source_filename=staged.name,
        blob_sha256=blob_hash,
        actor_id=actor.id,
    )
    link = provenance.attach_ingested_artifact(db_session, file_row, first)
    provenance.set_user_override(
        db_session,
        provenance_source_id=link.provenance_source_id,
        field_name="title",
        value="Local",
    )
    db_session.commit()
    job_id = registry.create(owner_user_id=actor.id)
    strategy = ingestion.IngestionStrategy(
        FileType.STL, True, lambda _path, _report: ({}, None), ()
    )
    engine = db_session.get_bind()
    assert isinstance(engine, Engine)
    ingestion.run_ingestion_pipeline(
        job_id=job_id,
        staged_path=staged,
        original_filename=staged.name,
        model_name="Ignored",
        collection=None,
        tags=None,
        source_hash=None,
        strategy=strategy,
        actor_user_id=actor.id,
        session_factory=SQLiteSessionFactory(engine),
        provenance_context=provenance.ProvenanceContext(
            manifest=manifest("Changed", "r2"),
            source_file_id="42:file",
            source_filename=staged.name,
            actor_id=actor.id,
        ),
    )
    assert db_session.exec(select(File).where(File.model_id == model.id)).all() == [
        file_row
    ]
    assert len(db_session.exec(select(ProvenanceCapture)).all()) == 2
    title = db_session.exec(
        select(ModelProvenanceField).where(
            ModelProvenanceField.provenance_source_id == link.provenance_source_id
        )
    ).one()
    assert provenance.effective_value(title) == "Local"


def test_failed_thumbnail_preserves_artifact_without_derived_pointer(
    db_session: Session,
    storage,
    model: Model,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_data: bytes) -> bytes:
        raise ValueError("corrupt image")

    monkeypatch.setattr(thumbnail, "to_webp", _boom)

    file_row = _persist(
        db_session, model, _staged(tmp_path), thumb_bytes=b"not-an-image"
    )

    db_session.refresh(model)
    assert file_row.id is not None
    assert Path(file_row.path).exists()
    assert file_row.thumbnail_path is None
    assert model.thumbnail_file_id is None
    assert (
        db_session.exec(select(Metadata).where(Metadata.file_id == file_row.id)).first()
        is not None
    )


def test_failed_metadata_does_not_leave_orphan_file_row(
    db_session: Session,
    storage,
    model: Model,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A File row without its Metadata is the silent-corruption case."""

    def _boom(*_args, **_kwargs):
        raise RuntimeError("metadata boom")

    # ``Metadata`` is only ever called to construct the row; model_fields is read
    # first, so keep that attribute intact.
    _boom.model_fields = ingestion.Metadata.model_fields
    monkeypatch.setattr(ingestion, "Metadata", _boom)

    with pytest.raises(RuntimeError, match="metadata boom"):
        _persist(db_session, model, _staged(tmp_path))

    db_session.rollback()
    assert db_session.exec(select(File).where(File.model_id == model.id)).all() == []
    assert not Path(storage.blob_key(model.slug, 1, "bracket.stl")).exists()


def test_persist_never_overwrites_an_unclaimed_destination(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    occupied = Path(storage.blob_key(model.slug, 1, "bracket.stl"))
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"pre-existing user data")

    file_row = _persist(db_session, model, _staged(tmp_path))

    assert occupied.read_bytes() == b"pre-existing user data"
    assert file_row.path != str(occupied)
    assert Path(file_row.path).read_bytes() == b"solid bracket\nendsolid\n"


def test_thumbnail_collision_preserves_existing_bytes_and_commits_artifact(
    db_session: Session,
    storage,
    model: Model,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    occupied = Path(storage.thumbnail_key(1))
    occupied.parent.mkdir(parents=True, exist_ok=True)
    occupied.write_bytes(b"user-owned thumbnail-shaped file")
    monkeypatch.setattr(storage, "thumbnail_key", lambda _file_id: str(occupied))

    file_row = _persist(
        db_session,
        model,
        _staged(tmp_path),
        thumb_bytes=(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
            b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        ),
    )

    assert occupied.read_bytes() == b"user-owned thumbnail-shaped file"
    assert Path(file_row.path).exists()
    assert file_row.thumbnail_path is None


def test_version_numbers_increment_across_revisions(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    first = _persist(db_session, model, _staged(tmp_path, "v1.stl"))
    second = _persist(db_session, model, _staged(tmp_path, "v2.stl"))

    assert (first.version, second.version) == (1, 2)


def test_concurrent_version_reservations_are_unique(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'versions.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        concurrent_model = Model(name="Concurrent", slug="concurrent", hash="c" * 64)
        session.add(concurrent_model)
        session.commit()
        session.refresh(concurrent_model)
        model_id = concurrent_model.id
    assert model_id is not None

    start = threading.Barrier(3)
    versions: list[int] = []
    errors: list[BaseException] = []

    def reserve() -> None:
        try:
            with Session(engine) as session:
                start.wait(timeout=5)
                version = ingestion._reserve_next_version(session, model_id)
                session.commit()
                versions.append(version)
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=10)

    try:
        assert errors == []
        assert sorted(versions) == [1, 2]
        with Session(engine) as session:
            assert session.get(Model, model_id).next_file_version == 3
    finally:
        engine.dispose()


def test_concurrent_artifacts_keep_distinct_versions_and_matching_hashes(
    tmp_path: Path, storage
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'artifacts.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        concurrent_model = Model(name="Race", slug="race", hash="a" * 64)
        session.add(concurrent_model)
        session.commit()
        session.refresh(concurrent_model)
        model_id = concurrent_model.id
    assert model_id is not None

    staged: list[tuple[Path, bytes]] = []
    for index in range(2):
        content = f"G28 ; artifact {index}\n".encode()
        path = tmp_path / f"race-{index}.gcode"
        path.write_bytes(content)
        staged.append((path, content))

    start = threading.Barrier(3)
    errors: list[BaseException] = []

    def persist(path: Path, content: bytes) -> None:
        try:
            with Session(engine) as session:
                model_row = session.get(Model, model_id)
                assert model_row is not None
                start.wait(timeout=5)
                ingestion.persist_artifact(
                    session,
                    model=model_row,
                    staged_path=path,
                    original_filename=path.name,
                    file_type=FileType.GCODE,
                    blob_hash=hashlib.sha256(content).hexdigest(),
                    meta={},
                    thumb_bytes=None,
                    overwrite_thumbnail=False,
                )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=persist, args=item) for item in staged]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    for thread in threads:
        thread.join(timeout=15)

    try:
        assert errors == []
        with Session(engine) as session:
            rows = session.exec(
                select(File).where(File.model_id == model_id).order_by(File.version)
            ).all()
        assert [row.version for row in rows] == [1, 2]
        assert sum(row.is_recommended for row in rows) == 1
        for row in rows:
            assert hashlib.sha256(Path(row.path).read_bytes()).hexdigest() == row.sha256
    finally:
        engine.dispose()


def test_thumbnail_is_written_and_selected(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    file_row = _persist(db_session, model, _staged(tmp_path), thumb_bytes=png)

    db_session.refresh(model)
    assert model.thumbnail_file_id == file_row.id
    assert Path(storage.thumbnail_key(file_row.id)).exists()


def test_concurrent_same_hash_upload_dedups_instead_of_crashing(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Model.hash is UNIQUE. Two uploads of the same bytes race between the
    lookup and the insert; the loser must dedup onto the winner's model, not
    500 with an IntegrityError."""
    from app.db.session import get_session_factory
    from app.services import storage as storage_mod

    dedup_hash = "c" * 64
    real_ensure = storage_mod.ensure_unique_slug

    def _insert_the_winner(base_slug, exists):
        # Runs after resolve_or_create_model's SELECT found nothing and before
        # its INSERT lands — exactly the window the race lives in.
        with get_session_factory().session() as other:
            other.add(Model(name="Winner", slug="winner", hash=dedup_hash))
            other.commit()
        return real_ensure(base_slug, exists)

    monkeypatch.setattr(ingestion.storage, "ensure_unique_slug", _insert_the_winner)

    model, created = ingestion.resolve_or_create_model(
        db_session, dedup_hash=dedup_hash, model_name="Loser"
    )

    assert created is False
    assert model.name == "Winner"
    assert (
        len(db_session.exec(select(Model).where(Model.hash == dedup_hash)).all()) == 1
    )


def test_durability_verification_reads_complete_artifact_from_fresh_transaction(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    file_row = _persist(db_session, model, _staged(tmp_path), thumb_bytes=png)
    assert model.id is not None and file_row.id is not None

    ingestion.verify_durable_artifact(
        _session_factory(db_session),
        model_id=model.id,
        file_id=file_row.id,
        thumbnail_status="generated",
    )

    assert Path(file_row.path).exists()
    assert Path(storage.thumbnail_key(file_row.id)).exists()


@pytest.mark.parametrize(
    "case",
    [
        pytest.param("missing-model", id="missing-model"),
        pytest.param("missing-artifact", id="missing-artifact"),
        pytest.param("missing-metadata", id="missing-metadata"),
        pytest.param("wrong-owner", id="wrong-owner"),
    ],
)
def test_durability_verification_rejects_incomplete_row_set(
    db_session: Session, storage, model: Model, tmp_path: Path, case: str
) -> None:
    file_row = _persist(db_session, model, _staged(tmp_path))
    assert model.id is not None and file_row.id is not None
    model_id = model.id
    file_id = file_row.id
    _break_durable_row_set(db_session, model, file_row, case)

    with pytest.raises(
        ingestion.ArtifactDurabilityError, match="artifact_rows_not_durable"
    ):
        ingestion.verify_durable_artifact(
            _session_factory(db_session),
            model_id=model_id,
            file_id=file_id,
            thumbnail_status="skipped",
        )


def test_durability_verification_rejects_missing_primary_blob(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    file_row = _persist(db_session, model, _staged(tmp_path))
    Path(file_row.path).unlink()
    assert model.id is not None and file_row.id is not None

    with pytest.raises(
        ingestion.ArtifactDurabilityError, match="artifact_blob_not_durable"
    ):
        ingestion.verify_durable_artifact(
            _session_factory(db_session),
            model_id=model.id,
            file_id=file_row.id,
            thumbnail_status="skipped",
        )


def test_durability_verification_rejects_missing_generated_thumbnail(
    db_session: Session, storage, model: Model, tmp_path: Path
) -> None:
    file_row = _persist(db_session, model, _staged(tmp_path))
    assert model.id is not None and file_row.id is not None

    with pytest.raises(
        ingestion.ThumbnailDurabilityError, match="thumbnail_blob_not_durable"
    ):
        ingestion.verify_durable_artifact(
            _session_factory(db_session),
            model_id=model.id,
            file_id=file_row.id,
            thumbnail_status="fallback_generated",
        )
