"""Independent processes must agree on the installation's one first owner."""

from sqlmodel import Session, SQLModel, create_engine, select

from app.db.models import User
from tests.fakes.setup_process import race_setup_workers


class TestFirstOwnerConcurrency:
    def test_two_sqlite_api_processes_create_exactly_one_owner(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'first-owner.sqlite'}"
        engine = create_engine(url)
        SQLModel.metadata.create_all(engine)
        try:
            results = race_setup_workers(url, tmp_path)
            assert sorted(code for code, _ in results) == [201, 409], results
            with Session(engine) as session:
                assert (
                    len(session.exec(select(User).where(User.is_superuser)).all()) == 1
                )
        finally:
            engine.dispose()
