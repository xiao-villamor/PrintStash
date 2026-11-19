"""First-run sample reservation and the geometry that enters real ingestion."""

from sqlmodel import select

from app.db.models import Job, Model, SystemConfig
from app.modules.ingestion import samples
from app.modules.ingestion.sample_geometry import sample_models


def test_first_run_reservation_survives_job_retention(db_session, make_user):
    admin = make_user(superuser=True)
    config = SystemConfig()
    db_session.add(config)
    db_session.flush()
    first = samples.queue_first_run(db_session, config)
    db_session.commit()

    assert first is not None
    job = db_session.get(Job, first)
    assert job is not None
    assert job.owner_user_id == admin.id
    db_session.delete(job)
    db_session.commit()
    db_session.refresh(config)

    assert samples.queue_first_run(db_session, config) is None
    assert db_session.exec(select(Job).where(Job.kind == samples.KIND)).all() == []


def test_first_run_skips_an_existing_library(db_session, make_user, make_model):
    make_user(superuser=True)
    make_model()
    config = SystemConfig()
    db_session.add(config)
    db_session.flush()

    assert samples.queue_first_run(db_session, config) is None
    assert config.samples_initialized
    assert db_session.exec(select(Model)).first() is not None


def test_sample_meshes_are_positive_watertight_solids():
    models = sample_models()
    assert len(models) == 3
    for name, mesh in models:
        assert mesh.is_watertight, name
        assert mesh.volume > 0, name
