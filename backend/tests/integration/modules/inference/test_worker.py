"""Native protocol and manifest validation reject untrusted bytes before publication."""

import json
from dataclasses import replace

import numpy as np
import pytest
from printstash_core.inference import EmbeddingError, EmbeddingInput
from pydantic import ValidationError

from app.modules.inference import manifest, worker
from app.modules.inference.onnx_cpu import OnnxCpuProvider
from tests.factories.embeddings import local_embedding_assets


@pytest.fixture
def assets(tmp_path):
    return local_embedding_assets(tmp_path / "assets")


class TestManifest:
    @pytest.mark.parametrize(
        "change", ["missing", "symlink", "large", "invalid", "key"]
    )
    def test_rejects_invalid_manifest(self, tmp_path, assets, change):
        path = assets / "manifest.json"
        if change == "missing":
            path.unlink()
        elif change == "symlink":
            target = tmp_path / "saved.json"
            path.rename(target)
            path.symlink_to(target)
        elif change == "large":
            path.write_bytes(b" " * (256 * 1024 + 1))
        elif change == "invalid":
            path.write_text("{")
        with pytest.raises(EmbeddingError, match="embedding_(manifest|model_key)"):
            manifest.read_manifest(
                assets, "different" if change == "key" else "two-tower-contract"
            )

    @pytest.mark.parametrize(
        "change",
        ["one_tower", "canary", "path", "digest", "dimension", "unknown", "nan"],
    )
    def test_rejects_incompatible_contract(self, assets, change):
        data = json.loads((assets / "manifest.json").read_text())
        if change == "one_tower":
            del data["text"]
        elif change == "canary":
            data["image"]["canary"] = [1]
        elif change == "path":
            data["image"]["graph"]["filename"] = "../outside"
        elif change == "digest":
            data["image"]["graph"]["sha256"] = "unchecked"
        elif change == "dimension":
            data["native_dimension"] = 4097
        elif change == "unknown":
            data["download_url"] = "https://invalid.test/model"
        else:
            data["image"]["canary"] = [float("nan"), 0, 0]
        with pytest.raises(ValidationError):
            manifest.LocalModelManifest.model_validate(data)

    @pytest.mark.parametrize(
        "change", ["missing", "symlink", "empty", "changed", "large"]
    )
    def test_verifies_preplaced_asset_bytes(self, assets, tmp_path, change):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        path = assets / "image.onnx"
        if change == "missing":
            path.unlink()
        elif change == "symlink":
            target = tmp_path / "saved.onnx"
            path.rename(target)
            path.symlink_to(target)
        elif change == "empty":
            path.write_bytes(b"")
        elif change == "changed":
            path.write_bytes(b"corrupt")
        else:
            with path.open("wb") as stream:
                stream.truncate(1024**3 + 1)
        with pytest.raises(EmbeddingError, match="embedding_asset_"):
            manifest.verify_assets(assets, contract)


class TestNativeProtocol:
    def test_runs_both_towers_from_bounded_protocol(self, assets, tmp_path):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        (tmp_path / "request.json").write_text(
            json.dumps(
                {
                    "config_hash": contract.space().config_hash,
                    "inputs": [
                        {"modality": "text", "text": "red"},
                        {"modality": "image", "width": 1, "height": 1},
                    ],
                }
            )
        )
        (tmp_path / "1.rgb").write_bytes(bytes([0, 0, 255]))
        worker.execute(tmp_path, assets, contract.model_key, 1)
        result = json.loads((tmp_path / "result.json").read_text())
        assert result["config_hash"] == contract.space().config_hash
        np.testing.assert_allclose(result["vectors"], [[1, 0, 0], [0, 0, 1]])

    @pytest.mark.parametrize("change", ["large", "space", "image_size"])
    def test_refuses_invalid_input_protocol(self, assets, tmp_path, change):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        data = {"config_hash": contract.space().config_hash, "inputs": []}
        if change == "space":
            data["config_hash"] = "a" * 64
        if change == "image_size":
            data["inputs"] = [{"modality": "image", "width": 1, "height": 1}]
            (tmp_path / "0.rgb").write_bytes(b"invalid")
        (tmp_path / "request.json").write_text(
            " " * (64 * 1024 + 1) if change == "large" else json.dumps(data)
        )
        with pytest.raises(EmbeddingError, match="embedding_(input|space)"):
            worker.execute(tmp_path, assets, contract.model_key, 1)
        assert not (tmp_path / "result.json").exists()

    @pytest.mark.parametrize("change", ["valid", "contract", "unexpected", "argv"])
    def test_sanitizes_worker_exit(self, assets, tmp_path, monkeypatch, change):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        (tmp_path / "request.json").write_text(
            json.dumps(
                {
                    "config_hash": "a" * 64
                    if change == "contract"
                    else contract.space().config_hash
                }
            )
        )
        if change == "unexpected":
            (tmp_path / "request.json").write_text("bad private source path")
        monkeypatch.setattr(
            worker.sys,
            "argv",
            ["worker"]
            if change == "argv"
            else ["worker", str(tmp_path), str(assets), contract.model_key, "1"],
        )
        status = worker.main()
        assert status == {"valid": 0, "contract": 3, "unexpected": 4, "argv": 2}[change]
        if change in ("contract", "unexpected"):
            assert json.loads((tmp_path / "error.json").read_text()) == {
                "code": "embedding_space_mismatch"
                if change == "contract"
                else "embedding_inference_failed"
            }

    @pytest.mark.parametrize(
        "change", ["threads", "space", "batch", "signature", "canary", "dino"]
    )
    def test_rejects_incompatible_native_operation(self, assets, change):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        if change == "signature":
            contract = contract.model_copy(
                update={
                    "image": contract.image.model_copy(
                        update={"output_name": "missing"}
                    )
                }
            )
        if change == "canary":
            contract = contract.model_copy(
                update={
                    "image": contract.image.model_copy(
                        update={"canary": (1.0, 0.0, 0.0)}
                    )
                }
            )
        if change == "dino":
            contract = contract.model_copy(update={"family": "dino", "text": None})
        code = {
            "threads": "thread_budget",
            "space": "space_mismatch",
            "batch": "batch_budget",
            "signature": "signature_mismatch",
            "canary": "canary_mismatch",
            "dino": "text_unavailable",
        }[change]
        with pytest.raises(EmbeddingError, match=code):
            provider = OnnxCpuProvider(
                assets, contract, 9 if change == "threads" else 1
            )
            provider.embed(
                () if change == "batch" else (EmbeddingInput("text", text="red"),),
                replace(provider.space, dimension=4)
                if change == "space"
                else provider.space,
            )


class TestNativeDependencies:
    @pytest.mark.parametrize("dependency", ["onnxruntime", "tokenizers"])
    def test_reports_unavailable_optional_runtime(
        self, assets, monkeypatch, dependency
    ):
        import sys

        contract = manifest.read_manifest(assets, "two-tower-contract")
        monkeypatch.setitem(sys.modules, dependency, None)
        with pytest.raises(EmbeddingError, match="embedding_runtime_unavailable"):
            OnnxCpuProvider(assets, contract, 1)

    def test_refuses_replaced_bytes_after_manifest_verification(self, assets):
        from app.modules.inference.onnx_cpu import _verified_bytes

        contract = manifest.read_manifest(assets, "two-tower-contract")
        manifest.verify_assets(assets, contract)
        (assets / contract.image.graph.filename).write_bytes(b"replaced graph")
        with pytest.raises(EmbeddingError, match="embedding_asset_digest_mismatch"):
            _verified_bytes(assets, contract.image.graph)

    def test_refuses_unexpected_native_input_name(self, assets):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        contract = contract.model_copy(
            update={
                "image": contract.image.model_copy(update={"input_name": "unavailable"})
            }
        )
        with pytest.raises(EmbeddingError, match="embedding_signature_mismatch"):
            OnnxCpuProvider(assets, contract, 1)
