"""The job owner records and cancels real HTTPS model transfers."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.errors import OperationError
from app.db.session import get_session_factory
from app.modules.search.configuration import update
from app.runtime import model_acquisition
from app.runtime.jobs import registry
from app.schemas.inference import SearchSettings
from tests.factories import build_user
from tests.fixtures.model_acquisition import model_host as _model_host  # noqa: F401


@pytest.fixture
def download_case(threaded_hub_db, model_host):
    fake, cache = model_host
    with get_session_factory().scoped_session() as session:
        actor = build_user(session, superuser=True)
        update(
            session,
            SearchSettings(
                enabled=True, local_models_enabled=True, download_enabled=True
            ),
        )
        session.commit()
        yield session, actor, fake, cache
    model_acquisition.close()


@pytest.fixture
def terminal_job():
    def wait(job_id):
        for _ in range(200):
            job = registry.get(job_id)
            if job.state in ("completed", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError("download did not reach a terminal state")

    return wait


class TestModelAcquisition:
    def test_records_a_failed_transfer(self, download_case, terminal_job):
        session, actor, fake, cache = download_case
        fake.fault = "corrupt"
        job_id = model_acquisition.start(session, actor, fake.entry.id)
        result = terminal_job(job_id)
        assert result.state == "failed"
        assert result.error == "embedding_asset_digest_mismatch"
        assert result.result == {"model_id": fake.entry.id, "error_code": result.error}
        assert not (cache / fake.entry.id).exists()
        assert not list(cache.glob(".download-*"))
        with pytest.raises(OperationError, match="embedding_download_not_running"):
            model_acquisition.cancel(job_id)

    @pytest.mark.parametrize("action", ["cancel", "shutdown"])
    def test_cancels_an_admitted_transfer(self, download_case, terminal_job, action):
        session, actor, fake, cache = download_case
        entered, release = threading.Event(), threading.Event()

        def hold_reply():
            entered.set()
            assert release.wait(5)

        fake.before_reply = hold_reply
        job_id = model_acquisition.start(session, actor, fake.entry.id)
        try:
            assert entered.wait(3)
            with ThreadPoolExecutor(1) as executor:
                stop = (
                    executor.submit(model_acquisition.close)
                    if action == "shutdown"
                    else None
                )
                if stop is None:
                    model_acquisition.cancel(job_id)
                else:
                    time.sleep(0.05)
                    assert not stop.done()
                release.set()
                if stop is not None:
                    stop.result(timeout=5)
            result = terminal_job(job_id)
            assert result.state == "failed"
            assert result.error == "embedding_download_cancelled"
            assert not (cache / fake.entry.id).exists()
            assert not list(cache.glob(".download-*"))
        finally:
            release.set()
