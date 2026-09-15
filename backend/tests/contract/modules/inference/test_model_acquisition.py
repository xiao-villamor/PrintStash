"""Real HTTPS file transfer, byte/digest limits, and atomic model installation."""

import pytest
from printstash_core.inference import EmbeddingError

from app.db.session import get_session_factory
from app.modules.inference.model_acquisition import Acquisition
from tests.fixtures.model_acquisition import model_host as _model_host  # noqa: F401


class TestAcquisition:
    def test_preserves_installed_models_after_interrupted_download(
        self, db_session, model_host
    ):
        from tests.factories.embeddings import text_embedding_assets

        fake, cache = model_host
        old = text_embedding_assets(cache / "old-model", pooling="mean")
        before = {path.name: path.read_bytes() for path in old.iterdir()}
        fake.fault = "corrupt"
        with pytest.raises(EmbeddingError, match="asset_digest_mismatch"):
            Acquisition(get_session_factory()).install(
                fake.entry.id, enabled=lambda: True
            )
        assert {path.name: path.read_bytes() for path in old.iterdir()} == before
        assert not list(cache.glob(".download-*"))

    def test_installs_a_verified_model_atomically(self, db_session, model_host):
        fake, cache = model_host
        installed = Acquisition(get_session_factory()).install(
            fake.entry.id, enabled=lambda: True
        )
        assert installed.id == fake.entry.id
        assert installed.directory == cache / fake.entry.id
        assert set(fake.calls) == {"text.onnx", "tokenizer.json"}
        assert not list(cache.glob(".download-*"))
        assert (installed.directory / "text.onnx").read_bytes() == (
            fake.directory / "text.onnx"
        ).read_bytes()

    @pytest.mark.parametrize(
        "fault,code",
        [
            ("corrupt", "asset_digest_mismatch"),
            ("oversize", "download_size_invalid"),
            ("short", "download_size_invalid"),
            ("redirect", "download_url_forbidden"),
            ("loop", "download_redirect_limit"),
        ],
    )
    def test_rejects_invalid_downloads(self, db_session, model_host, fault, code):
        fake, cache = model_host
        fake.fault = fault
        with pytest.raises(EmbeddingError, match=code):
            Acquisition(get_session_factory()).install(
                fake.entry.id, enabled=lambda: True
            )
        assert not (cache / fake.entry.id).exists()
        assert not list(cache.glob(".download-*"))
        assert len(fake.calls) <= 6

    def test_never_downloads_without_acquisition_opt_in(self, db_session, model_host):
        fake, cache = model_host
        with pytest.raises(EmbeddingError, match="download_disabled"):
            Acquisition(get_session_factory()).install(
                fake.entry.id, enabled=lambda: False
            )
        assert fake.calls == []
        assert not cache.exists()

    def test_cleans_up_a_cancelled_download(self, db_session, model_host):
        fake, cache = model_host
        with pytest.raises(EmbeddingError, match="download_cancelled"):
            Acquisition(get_session_factory()).install(
                fake.entry.id,
                enabled=lambda: True,
                cancelled=lambda: len(fake.calls) >= 1,
            )
        assert not (cache / fake.entry.id).exists()
        assert not list(cache.glob(".download-*"))

    def test_recovers_an_abandoned_install(self, db_session, model_host):
        fake, cache = model_host
        abandoned = cache / (".download-" + "a" * 32)
        abandoned.mkdir(parents=True)
        (abandoned / "partial.onnx").write_bytes(b"partial")
        installed = Acquisition(get_session_factory()).install(
            fake.entry.id, enabled=lambda: True
        )
        assert installed.id == fake.entry.id
        assert not abandoned.exists()

    def test_reuses_a_verified_install_without_egress(self, db_session, model_host):
        fake, cache = model_host
        acquisition = Acquisition(get_session_factory())
        first = acquisition.install(fake.entry.id, enabled=lambda: True)
        count = len(fake.calls)
        second = acquisition.install(fake.entry.id, enabled=lambda: True)
        assert first == second
        assert len(fake.calls) == count

    def test_keeps_download_credentials_out_of_logs(
        self, db_session, model_host, caplog
    ):
        fake, _ = model_host
        caplog.set_level("DEBUG")
        fake.fault = "redirect"
        with pytest.raises(EmbeddingError):
            Acquisition(get_session_factory()).install(
                fake.entry.id, enabled=lambda: True
            )
        assert "test-signed-secret" not in caplog.text

    @pytest.mark.parametrize("model_host", ["sparse"], indirect=True)
    def test_acquires_a_sparse_export_through_the_shared_cache(
        self, db_session, model_host
    ):
        fake, cache = model_host
        installed = Acquisition(get_session_factory()).install(
            fake.entry.id, enabled=lambda: True
        )
        assert installed.id == fake.entry.id
        assert installed.manifest.family == "splade"
        assert set(fake.calls) == {"model.onnx", "tokenizer.json"}
        assert (cache / fake.entry.id / "model.onnx").read_bytes() == (
            fake.directory / "model.onnx"
        ).read_bytes()
