"""Query scheduling and RAM cache limits are independent of provider runtime."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event

import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput, EmbeddingSpace

from app.modules.inference.query import QueryRunner
from tests.fakes.embedding_provider import RecordingEmbeddingProvider


@pytest.fixture
def query_runtime():
    provider = RecordingEmbeddingProvider()
    runner = QueryRunner(workers=1, cache_entries=1, ttl=60)
    space = EmbeddingSpace("test", "v1", 4, "text", "v1")
    try:
        yield runner, provider, space
    finally:
        if provider.release:
            provider.release.set()
        runner.close()


class TestQueryRunner:
    def test_sanitizes_an_unexpected_provider_failure(self, query_runtime):
        runner, provider, space = query_runtime
        provider.error = RuntimeError("private upstream payload")

        with pytest.raises(EmbeddingError, match="^inference_query_unavailable$"):
            runner.embed(
                provider,
                space,
                EmbeddingInput("text", text="boat"),
                authorization="reader",
                seconds=1,
            )

    @pytest.mark.parametrize("vectors", [(), ((1.0,),), ((float("nan"), 0, 0, 0),)])
    def test_rejects_invalid_query_vectors(self, query_runtime, vectors):
        runner, provider, space = query_runtime
        provider.vectors = vectors

        with pytest.raises(EmbeddingError):
            runner.embed(
                provider,
                space,
                EmbeddingInput("text", text="boat"),
                authorization="reader",
                seconds=1,
            )

    def test_rejects_images_from_the_text_cache(self, query_runtime):
        runner, provider, space = query_runtime

        with pytest.raises(EmbeddingError, match="embedding_image_unavailable"):
            runner.embed(
                provider,
                space,
                EmbeddingInput("image", rgb=b"\0\0\0", width=1, height=1),
                authorization="reader",
                seconds=1,
            )

        assert provider.calls == []

    def test_caches_query_vectors_in_memory(self, query_runtime):
        runner, provider, space = query_runtime
        value = EmbeddingInput("text", text="a private boat")

        first = runner.embed(provider, space, value, authorization="reader", seconds=1)
        second = runner.embed(provider, space, value, authorization="reader", seconds=1)

        assert first == second == (1, 0, 0, 0)
        assert len(provider.calls) == 1

    def test_runs_compatible_images_without_caching_inputs_or_vectors(
        self, query_runtime
    ):
        runner, provider, original = query_runtime
        space = replace(original, modality="text_image")
        value = EmbeddingInput("image", rgb=b"\x12\x34\x56", width=1, height=1)

        first = runner.embed(provider, space, value, authorization="reader", seconds=1)
        provider.vectors = ((0, 1, 0, 0),)
        second = runner.embed(provider, space, value, authorization="reader", seconds=1)

        assert first == (1, 0, 0, 0)
        assert second == (0, 1, 0, 0)
        assert len(provider.calls) == 2
        assert runner._cache == {}

    @pytest.mark.parametrize(
        "changed",
        ["space", "user", "permissions"],
        ids=["space", "user", "permissions"],
    )
    def test_isolates_query_cache_contexts(self, query_runtime, changed):
        runner, provider, space = query_runtime
        value = EmbeddingInput("text", text="a private boat")
        runner.embed(provider, space, value, authorization="reader:v1", seconds=1)

        runner.embed(
            provider,
            replace(space, model_revision="v2") if changed == "space" else space,
            value,
            authorization="reader:v1"
            if changed == "space"
            else "other:v1"
            if changed == "user"
            else "reader:v2",
            seconds=1,
        )

        assert len(provider.calls) == 2

    def test_evicts_the_least_recent_query(self, query_runtime):
        runner, provider, space = query_runtime
        for text in ("boat", "bracket", "boat"):
            runner.embed(
                provider,
                space,
                EmbeddingInput("text", text=text),
                authorization="reader",
                seconds=1,
            )

        assert [batch[0].text for batch in provider.calls] == [
            "boat",
            "bracket",
            "boat",
        ]

    def test_expires_cached_query_vectors(self, query_runtime, monkeypatch):
        from app.modules.inference import query

        runner, provider, space = query_runtime
        value = EmbeddingInput("text", text="boat")
        runner.embed(provider, space, value, authorization="reader", seconds=1)
        original = query.time.monotonic
        monkeypatch.setattr(query.time, "monotonic", lambda: original() + 61)

        runner.embed(provider, space, value, authorization="reader", seconds=1)

        assert len(provider.calls) == 2

    def test_bounds_interactive_inference(self, query_runtime):
        runner, provider, space = query_runtime
        provider.release = Event()
        value = EmbeddingInput("text", text="boat")
        with ThreadPoolExecutor(max_workers=1) as requests:
            pending = requests.submit(
                runner.embed, provider, space, value, authorization="reader", seconds=3
            )
            assert provider.started.wait(1)
            try:
                with pytest.raises(EmbeddingError, match="inference_query_busy"):
                    runner.embed(
                        provider, space, value, authorization="reader", seconds=1
                    )
            finally:
                provider.release.set()
            assert pending.result(timeout=1) == (1, 0, 0, 0)
        assert len(provider.calls) == 1

    def test_returns_by_query_deadline(self, query_runtime):
        runner, provider, space = query_runtime
        provider.release = Event()

        with pytest.raises(EmbeddingError, match="inference_timeout"):
            runner.embed(
                provider,
                space,
                EmbeddingInput("text", text="boat"),
                authorization="reader",
                seconds=0.02,
            )

        assert not provider.release.is_set()
        assert len(provider.calls) == 1

    def test_rejects_queries_after_shutdown(self, query_runtime):
        runner, provider, space = query_runtime
        runner.close()

        with pytest.raises(EmbeddingError, match="inference_query_unavailable"):
            runner.embed(
                provider,
                space,
                EmbeddingInput("text", text="boat"),
                authorization="reader",
                seconds=1,
            )

        assert provider.calls == []
