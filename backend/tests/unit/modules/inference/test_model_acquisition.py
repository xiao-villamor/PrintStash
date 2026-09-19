"""Download admission rejects unsafe URLs before a client sees them."""

import hashlib

import httpx
import pytest
from printstash_core.inference import EmbeddingError

from app.modules.inference.model_acquisition import DownloadPolicy, fetch
from app.modules.inference.model_registry import DownloadAsset


class TestDownloadPolicy:
    @pytest.mark.parametrize(
        "host",
        [
            "us.aws.cdn.hf.co",
            "us.gcp.cdn.hf.co",
            "cdn-lfs-us-1.hf.co",
            "cdn-lfs-eu-1.hf.co",
        ],
    )
    def test_accepts_official_file_delivery_hosts(self, host):
        url = f"https://{host}/model?signature=test"
        assert DownloadPolicy().validate(url) == url

    @pytest.mark.parametrize(
        "url",
        [
            "http://huggingface.co/model",
            "http://us.aws.cdn.hf.co/model",
            "https://us.aws.cdn.hf.co.evil.test/model",
            "https://evil.us.aws.cdn.hf.co/model",
            "https://us.aws.cdn.hf.co@evil.test/model",
            "https://user:secret@us.aws.cdn.hf.co/model",
            "https://us.aws.cdn.hf.co:8443/model",
            "https://huggingface.co.evil.test/model",
            "https://user:secret@huggingface.co/model",
            "https://huggingface.co:8443/model",
            "https://huggingface.co/model#fragment",
            "https://huggingface.co/model\nsecret",
            "https://127.0.0.1/model",
            "file:///private/model",
            "https://huggingface.co:" + "9" * 20,
        ],
    )
    def test_rejects_hostile_model_download_urls(self, url):
        with pytest.raises(EmbeddingError, match="download_url_forbidden"):
            DownloadPolicy().validate(url)

    def test_accepts_an_explicit_https_mirror(self):
        policy = DownloadPolicy("https://mirror.test:9443/models")
        assert (
            policy.validate("https://mirror.test:9443/object")
            == "https://mirror.test:9443/object"
        )
        assert policy.validate("https://cas-bridge.xethub.hf.co/object?signature=test")
        with pytest.raises(EmbeddingError):
            policy.validate("https://mirror.test:9444/object")

    @pytest.mark.parametrize(
        "mirror",
        [
            "http://mirror.test",
            "https://user:secret@mirror.test",
            "https://mirror.test/?token=secret",
        ],
    )
    def test_rejects_invalid_mirror_configuration(self, mirror):
        with pytest.raises(EmbeddingError, match="download_url_forbidden"):
            DownloadPolicy(mirror).validate("https://huggingface.co/model")


class TestFetch:
    def test_downloads_a_verified_file_through_a_cdn_redirect(self, tmp_path):
        content = b"pinned model bytes"
        asset = DownloadAsset(
            "model.onnx",
            "onnx/model.onnx",
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
        target = tmp_path / asset.filename
        source = "https://huggingface.co/repo/resolve/revision/onnx/model.onnx"
        destination = "https://us.aws.cdn.hf.co/model?signature=test"
        progress = []

        def respond(request: httpx.Request) -> httpx.Response:
            if str(request.url) == source:
                return httpx.Response(302, headers={"location": destination})
            assert str(request.url) == destination
            return httpx.Response(200, stream=httpx.ByteStream(content))

        with httpx.Client(transport=httpx.MockTransport(respond)) as client:
            fetch(
                client,
                DownloadPolicy(),
                source,
                asset,
                target,
                lambda: None,
                progress.append,
            )
        assert target.read_bytes() == content
        assert sum(progress) == len(content)
