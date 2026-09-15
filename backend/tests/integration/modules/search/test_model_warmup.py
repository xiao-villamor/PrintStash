"""Active local exports warm from installed files without query content or egress."""

import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput
from sqlmodel import select

from app.db.models import IndexGeneration
from app.db.session import get_session_factory
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.query import QueryRunner
from app.modules.search import configuration
from app.modules.search.model_warmup import ModelWarmup
from tests.paths import BACKEND_DIR


@pytest.fixture
def delayed_model(warm_model, monkeypatch, tmp_path):
    model, _ = warm_model
    provider = LocalEmbeddingProvider(
        get_session_factory(), model.directory, model.manifest.model_key, 1
    )
    release, entered, processes = tmp_path / "release", threading.Event(), []

    def spawn():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.fakes.cold_embedding_worker",
                str(model.directory),
                model.manifest.model_key,
                "1",
                str(release),
            ],
            cwd=BACKEND_DIR,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
        )
        processes.append(process)
        entered.set()
        return process

    monkeypatch.setattr(provider, "_spawn", spawn)
    processor = ModelWarmup(get_session_factory(), provider_factory=lambda *_: provider)
    yield processor, provider, entered, release, processes
    processor.stop()
    release.touch()


class TestModelWarmup:
    @pytest.mark.parametrize("invalid", ["metadata", "stopped"])
    def test_skips_unusable_active_models(self, db_session, warm_model, invalid):
        from app.db.models import EmbeddingSpace

        _, generation = warm_model
        processor = ModelWarmup(get_session_factory())
        if invalid == "stopped":
            processor.stop()
        else:
            stored = db_session.get(EmbeddingSpace, generation.space_id)
            stored.config_json = "{"
            db_session.add(stored)
            db_session.commit()
        try:
            assert processor.work_one() is False
        finally:
            processor.stop()

    def test_preserves_the_loader_during_cold_queries(self, db_session, delayed_model):
        processor, provider, entered, release, processes = delayed_model
        runner = QueryRunner()
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(processor.work_one)
            try:
                assert entered.wait(3)
                with pytest.raises(EmbeddingError, match="embedding_model_warming"):
                    runner.embed(
                        provider,
                        provider.space,
                        EmbeddingInput("text", text="red"),
                        authorization="user",
                        seconds=0.05,
                    )
                assert processes[0].poll() is None
                assert not future.done()
                release.touch()
                assert future.result(timeout=15)
                assert provider.is_warm
            finally:
                processor.stop()
                release.touch()
                runner.close()

    def test_cancels_loading_after_local_consent_is_revoked(
        self, db_session, delayed_model
    ):
        processor, provider, entered, release, processes = delayed_model
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(processor.work_one)
            try:
                assert entered.wait(3)
                configuration.update(
                    db_session,
                    configuration.settings(db_session).model_copy(
                        update={"local_models_enabled": False}
                    ),
                )
                db_session.commit()
                assert future.result(timeout=5)
                assert processes[0].poll() is not None
                assert not provider.is_warm
            finally:
                processor.stop()
                release.touch()

    def test_serves_queries_after_background_warmup(self, db_session, warm_model):
        model, _ = warm_model
        processor = ModelWarmup(get_session_factory())
        assert processor.work_one()
        provider = LocalEmbeddingProvider(
            get_session_factory(), model.directory, model.manifest.model_key, 1
        )
        runner = QueryRunner()
        try:
            assert provider.is_warm
            assert runner.embed(
                provider,
                provider.space,
                EmbeddingInput("text", text="red"),
                authorization="user",
                seconds=1,
            ) == (1, 0, 0)
            assert not processor.work_one()
        finally:
            runner.close()
            processor.stop()

    @pytest.mark.parametrize("off", ["enabled", "local_models_enabled", "inactive"])
    def test_warms_only_active_locally_permitted_models(
        self, db_session, warm_model, off
    ):
        model, _ = warm_model
        if off == "inactive":
            generation = db_session.exec(select(IndexGeneration)).one()
            generation.state, generation.active_profile_key = "retired", None
            db_session.add(generation)
        else:
            configuration.update(
                db_session,
                configuration.settings(db_session).model_copy(update={off: False}),
            )
        db_session.commit()
        processor = ModelWarmup(get_session_factory())
        assert not processor.work_one()
        provider = LocalEmbeddingProvider(
            get_session_factory(), model.directory, model.manifest.model_key, 1
        )
        assert not provider.is_warm
        processor.stop()
