"""Manufacturing snapshots survive edits to the composition they came from."""

from app.services import multipart_builds
from tests.fakes.manufacturing import race_confirmations, seed_confirmation_race


class TestManufacturingSnapshot:
    def test_historical_requirements_ignore_later_composition_edits(
        self,
        db_session,
        make_user,
        make_multipart_model,
        make_model,
        make_multipart_part,
        make_multipart_build,
        make_multipart_build_part,
    ):
        owner = make_user(superuser=True)
        composition = make_multipart_model()
        model = make_model()
        source_part = make_multipart_part(composition, quantity=4)
        build = make_multipart_build(composition, object_quantity=2)
        make_multipart_build_part(build, model, quantity=4)
        source_part.quantity = 7
        db_session.add(source_part)
        db_session.commit()
        assert (
            multipart_builds.read(db_session, owner, build).parts[0].required_units == 8
        )


class TestManufacturingConcurrency:
    def test_concurrent_conflicting_confirmations_have_one_winner(self, tmp_path):
        from sqlmodel import create_engine

        engine = create_engine(f"sqlite:///{tmp_path / 'results.sqlite'}")
        try:
            ids = seed_confirmation_race(engine)
            outcomes = race_confirmations(engine, *ids, False)
            assert sorted(code for code, _ in outcomes) == [200, 409]
            assert (200, 1) in outcomes
        finally:
            engine.dispose()

    def test_concurrent_duplicate_confirmations_count_output_once(self, tmp_path):
        from sqlmodel import Session, create_engine, select

        from app.db.models import MultipartBuildConfirmation

        engine = create_engine(f"sqlite:///{tmp_path / 'duplicates.sqlite'}")
        try:
            ids = seed_confirmation_race(engine)
            assert race_confirmations(engine, *ids, True) == [(200, 1), (200, 1)]
            with Session(engine) as session:
                assert len(session.exec(select(MultipartBuildConfirmation)).all()) == 1
        finally:
            engine.dispose()


class TestConcurrentReservations:
    def test_parallel_queue_requests_reserve_each_unit_once(self, tmp_path):
        from sqlmodel import create_engine

        from tests.fakes.manufacturing import race_queues

        engine = create_engine(f"sqlite:///{tmp_path / 'queue.sqlite'}")
        try:
            outcomes, reserved = race_queues(engine)
            assert outcomes == [(201, 1), (409, 0)]
            assert reserved == 4
        finally:
            engine.dispose()
