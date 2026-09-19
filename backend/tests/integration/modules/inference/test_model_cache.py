"""Real cache files, generation references, and concurrent load exclusion."""

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from printstash_core.inference import EmbeddingError

from app.core.config import _overlay
from app.modules.inference import model_cache
from app.modules.inference.manifest import read_manifest
from tests.factories.embeddings import text_embedding_assets


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    root.mkdir()
    monkeypatch.setitem(_overlay, "embedding_cache_dir", root)
    monkeypatch.setitem(_overlay, "embedding_local_model_dir", "")
    return root


@pytest.fixture
def installed(cache):
    directory = text_embedding_assets(cache / "preplaced")
    return model_cache.inspect(directory)


class TestModelCache:
    @pytest.mark.parametrize("create", [False, True], ids=["open", "create"])
    def test_rejects_a_file_in_place_of_the_cache(self, tmp_path, create):
        path = tmp_path / "cache"
        path.write_text("preserve")

        with pytest.raises(EmbeddingError, match="embedding_cache_unavailable"):
            model_cache.safe_directory(path, create=create)

        assert path.read_text() == "preserve"

    @pytest.mark.parametrize(
        "body",
        [b"{}", b"[]", b"{", b"x" * (256 * 1024 + 1)],
        ids=["missing-key", "wrong-shape", "invalid-json", "oversized"],
    )
    def test_rejects_invalid_cached_manifests(self, cache, body):
        (cache / "manifest.json").write_bytes(body)

        with pytest.raises(
            EmbeddingError, match="embedding_manifest_(invalid|unavailable)"
        ):
            model_cache.inspect(cache)

        assert (cache / "manifest.json").read_bytes() == body

    def test_bounds_the_model_inventory(self, cache):
        directories = [cache / str(index) for index in range(65)]
        [directory.mkdir() for directory in directories]

        with pytest.raises(EmbeddingError, match="embedding_cache_entry_limit"):
            model_cache.inventory()

        assert all(directory.is_dir() for directory in directories)

    def test_bounds_files_during_capacity_calculation(self, db_session, cache):
        staging = cache / ".staging"
        staging.mkdir()
        files = [staging / str(index) for index in range(1025)]
        [path.touch() for path in files]

        with pytest.raises(EmbeddingError, match="embedding_cache_entry_limit"):
            model_cache.make_room(db_session, 1)

        assert all(path.exists() for path in files)

    def test_refuses_cache_symlinks_during_capacity_calculation(
        self, db_session, cache, tmp_path
    ):
        outside = tmp_path / "outside"
        outside.write_text("preserve")
        (cache / ".unknown").symlink_to(outside)

        with pytest.raises(EmbeddingError, match="embedding_cache_path_invalid"):
            model_cache.make_room(db_session, 1)

        assert outside.read_text() == "preserve"

    def test_bounds_waiting_for_a_pinned_model_cache(self, cache):
        with model_cache.cache_lock(exclusive=True):
            with pytest.raises(EmbeddingError, match="embedding_compute_busy"):
                with model_cache.cache_lock():
                    pytest.fail("a reader cannot acquire an exclusively held cache")

        with model_cache.cache_lock() as root:
            assert root == cache

    def test_discovers_preplaced_models_offline(self, installed):
        model = model_cache.verify(installed.id)
        assert model.manifest.model_key == "text-contract"
        assert model.size == sum(
            path.stat().st_size for path in model.directory.iterdir()
        )

    @pytest.mark.parametrize("path_kind", ["root", "directory", "asset", "traversal"])
    def test_rejects_cache_path_escape(
        self, cache, installed, tmp_path, monkeypatch, path_kind
    ):
        outside = tmp_path / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("unchanged")
        if path_kind == "root":
            link = tmp_path / "symlink"
            link.symlink_to(outside, target_is_directory=True)
            monkeypatch.setitem(_overlay, "embedding_cache_dir", link)
            with pytest.raises(EmbeddingError, match="path_invalid"):
                model_cache.inventory()
        else:
            if path_kind == "directory":
                link = cache / "outside-link"
                link.symlink_to(outside, target_is_directory=True)
                with pytest.raises(EmbeddingError, match="path_invalid"):
                    model_cache.inspect(link)
            elif path_kind == "asset":
                path = installed.directory / "text.onnx"
                path.unlink()
                path.symlink_to(sentinel)
                assert model_cache.inventory() == ()
            else:
                path = installed.directory / "manifest.json"
                value = json.loads(path.read_text())
                value["text"]["graph"]["filename"] = "../../outside/sentinel"
                path.write_text(json.dumps(value))
                assert model_cache.inventory() == ()
        assert sentinel.read_text() == "unchanged"

    @pytest.mark.parametrize("state", ["active", "building", "retired", "pruning"])
    def test_refuses_pruning_referenced_models(
        self, db_session, installed, make_embedding_space, make_index_generation, state
    ):
        contract = replace(installed.manifest.space(), query_prefix="query: ")
        row = make_embedding_space(
            config_hash=contract.config_hash, config_json=json.dumps(contract.__dict__)
        )
        make_index_generation(row, state=state, active=state == "active")
        with pytest.raises(EmbeddingError, match="model_in_use"):
            model_cache.remove(db_session, installed.id)
        assert (
            read_manifest(installed.directory, "text-contract").space()
            == installed.manifest.space()
        )

    def test_prunes_unreferenced_models_by_lru(
        self, db_session, cache, installed, monkeypatch
    ):
        newer = model_cache.inspect(
            text_embedding_assets(cache / "newer", pooling="mean")
        )
        os.utime(installed.directory, (1, 1))
        monkeypatch.setitem(_overlay, "embedding_cache_max_bytes", newer.size + 100)
        with model_cache.cache_lock(exclusive=True):
            model_cache.make_room(db_session, 50)
        assert not installed.directory.exists()
        assert model_cache.resolve(newer.id).directory == newer.directory

    def test_protects_models_during_concurrent_load(self, db_session, installed):
        started = threading.Event()

        def prune():
            started.set()
            model_cache.remove(db_session, installed.id)

        with ThreadPoolExecutor(1) as executor:
            with model_cache.pin(installed.directory):
                future = executor.submit(prune)
                assert started.wait(1)
                assert installed.directory.exists()
                assert not future.done()
            future.result(timeout=5)
        assert not installed.directory.exists()

    def test_refuses_eviction_of_referenced_models_for_capacity(
        self,
        db_session,
        cache,
        installed,
        monkeypatch,
        make_embedding_space,
        make_index_generation,
    ):
        # Cache capacity cannot reclaim a referenced model to fit the next one.
        contract = installed.manifest.space()
        row = make_embedding_space(config_json=json.dumps(contract.__dict__))
        make_index_generation(row)
        monkeypatch.setitem(_overlay, "embedding_cache_max_bytes", installed.size)
        with (
            model_cache.cache_lock(exclusive=True),
            pytest.raises(EmbeddingError, match="cache_budget"),
        ):
            model_cache.make_room(db_session, 1)
        assert installed.directory.exists()
