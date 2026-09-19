"""Native protocol and manifest validation reject untrusted bytes before publication."""

import base64
import json
from dataclasses import replace
from io import BytesIO

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
    def test_visual_profiles_use_the_exact_paired_encoder(self, assets):
        from printstash_core.search.visual_inputs import VisualRecipe

        contract = manifest.read_manifest(assets, "two-tower-contract")
        derived = VisualRecipe.space(
            contract.space(), image_size=32, profile="multiview"
        )
        manifest.validate_space(contract, derived)
        changed = replace(derived, model_revision="another-tower")
        with pytest.raises(EmbeddingError, match="embedding_space_mismatch"):
            manifest.validate_space(contract, changed)

    def test_preserves_legacy_visual_space_identity(self):
        from tests.paths import FIXTURES_DIR

        payload = (
            FIXTURES_DIR / "embeddings" / "clip-vit-base-patch32-fp32.json"
        ).read_bytes()
        # Verified against the implementation at main c11db102 before changing it.
        assert (
            manifest.LocalModelManifest.model_validate_json(payload).space().config_hash
            == "7f56e23951620776f8a65d4de5441b6ff1eecd1f48c8ddf1eca8a82f1dea2089"
        )

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
    @pytest.mark.parametrize("profile", ["cls", "mean", "point", "sparse"])
    def test_executes_every_supported_manifest_family(self, tmp_path, profile):
        import math

        from printstash_core.inference.points import canary_input

        from tests.factories.embeddings import (
            point_embedding_assets,
            sparse_embedding_assets,
            text_embedding_assets,
        )

        if profile == "point":
            directory = point_embedding_assets(tmp_path / "point")
            key = "point-contract"
        elif profile == "sparse":
            directory = sparse_embedding_assets(tmp_path / "sparse")
            key = "sparse-contract"
        else:
            directory = text_embedding_assets(tmp_path / "text", pooling=profile)
            key = "text-contract"
        contract = manifest.read_manifest(directory, key)
        identity = manifest.manifest_identity(contract)
        native = worker.NativeWorker(directory, key, 1)
        if profile == "sparse":
            payload = {"config_hash": identity, "sparse_text": "bicycle bracket"}
        elif profile == "point":
            payload = {
                "config_hash": identity,
                "inputs": [
                    {
                        "modality": "point_cloud",
                        "points_base64": base64.b64encode(
                            canary_input().points
                        ).decode(),
                    }
                ],
            }
        else:
            payload = {
                "config_hash": identity,
                "inputs": [{"modality": "text", "text": "red blue"}],
            }
        result = json.loads(native.execute(json.dumps(payload).encode()))
        assert result["config_hash"] == identity
        if profile == "sparse":
            assert {
                item["term"]: item["weight"] for item in result["terms"]
            } == pytest.approx(
                {
                    "bicycle": math.log(4),
                    "bike": math.log(3),
                    "bracket": math.log(4),
                    "mount": math.log(3),
                }
            )
            assert result["truncated"] is False
            payload["sparse_text"] = None
            assert (
                json.loads(native.execute(json.dumps(payload).encode()))["terms"] == []
            )
        else:
            expected = {
                "cls": [1, 0, 0],
                "mean": [2**-0.5, 0, 2**-0.5],
                "point": [3**-0.5] * 3,
            }[profile]
            np.testing.assert_allclose(result["vectors"], [expected], atol=1e-6)
            assert result["truncated"] == [False]

    @pytest.mark.parametrize("fault", ["identity", "inputs", "space"])
    def test_rejects_cross_profile_sparse_envelopes(self, tmp_path, fault):
        from tests.factories.embeddings import sparse_embedding_assets

        directory = sparse_embedding_assets(tmp_path / "sparse")
        contract = manifest.read_manifest(directory, "sparse-contract")
        payload = {"config_hash": manifest.manifest_identity(contract)}
        if fault == "identity":
            payload["config_hash"] = "f" * 64
        elif fault == "inputs":
            payload["inputs"] = [{"modality": "text", "text": "red"}]
        else:
            payload["space_json"] = "{}"
        with pytest.raises(EmbeddingError, match="embedding_space_mismatch"):
            worker.NativeWorker(directory, "sparse-contract", 1).execute(
                json.dumps(payload).encode()
            )

    @pytest.mark.parametrize(
        "frame", [b"\x00", b"\xff\xff\xff\xff", b"\x00\x00\x00\x02x"]
    )
    def test_bounds_worker_pipe_frames(self, frame):
        output = BytesIO()
        assert worker.serve(BytesIO(frame), output) == 2
        assert output.getvalue() == b""

    def test_serves_multiple_private_requests(self, assets, monkeypatch):
        import struct

        contract = manifest.read_manifest(assets, "two-tower-contract")
        monkeypatch.setattr(
            worker.sys,
            "argv",
            ["worker", str(assets), contract.model_key, "1", "--persistent"],
        )
        frames = []
        for color in ("red", "blue"):
            payload = json.dumps(
                {
                    "config_hash": contract.space().config_hash,
                    "inputs": [{"modality": "text", "text": color}],
                }
            ).encode()
            frames.append(struct.pack("!I", len(payload)) + payload)
        output = BytesIO()
        assert worker.serve(BytesIO(b"".join(frames)), output) == 0
        output.seek(0)
        values = []
        for _ in range(2):
            length = struct.unpack("!I", output.read(4))[0]
            values.append(json.loads(output.read(length))["vectors"])
        assert values == [[[1, 0, 0]], [[0, 0, 1]]]
        assert output.read() == b""

    def test_runs_both_towers_from_bounded_protocol(self, assets):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        payload = json.dumps(
            {
                "config_hash": contract.space().config_hash,
                "inputs": [
                    {"modality": "text", "text": "red"},
                    {
                        "modality": "image",
                        "width": 1,
                        "height": 1,
                        "rgb_base64": base64.b64encode(bytes([0, 0, 255])).decode(),
                    },
                ],
            }
        ).encode()

        result = json.loads(worker.execute(payload, assets, contract.model_key, 1))

        assert result["config_hash"] == contract.space().config_hash
        np.testing.assert_allclose(result["vectors"], [[1, 0, 0], [0, 0, 1]])

    @pytest.mark.parametrize("change", ["large", "space", "image_size", "base64"])
    def test_refuses_invalid_input_protocol(self, assets, change):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        data = {"config_hash": contract.space().config_hash, "inputs": []}
        if change == "space":
            data["config_hash"] = "a" * 64
        if change in ("image_size", "base64"):
            data["inputs"] = [
                {
                    "modality": "image",
                    "width": 1,
                    "height": 1,
                    "rgb_base64": "bad!"
                    if change == "base64"
                    else base64.b64encode(b"invalid").decode(),
                }
            ]
        payload = (
            b" " * (worker.MAX_INPUT_BYTES + 1)
            if change == "large"
            else json.dumps(data).encode()
        )

        with pytest.raises(EmbeddingError, match="embedding_(input|space)"):
            worker.execute(payload, assets, contract.model_key, 1)

    @pytest.mark.parametrize("change", ["valid", "contract", "unexpected", "argv"])
    def test_sanitizes_worker_exit(self, assets, monkeypatch, change):
        contract = manifest.read_manifest(assets, "two-tower-contract")
        payload = json.dumps(
            {
                "config_hash": "a" * 64
                if change == "contract"
                else contract.space().config_hash
            }
        ).encode()
        if change == "unexpected":
            payload = b"bad private query"
        monkeypatch.setattr(
            worker.sys,
            "argv",
            ["worker"]
            if change == "argv"
            else ["worker", str(assets), contract.model_key, "1"],
        )
        output = BytesIO()

        status = worker.main(BytesIO(payload), output)

        assert status == {"valid": 0, "contract": 3, "unexpected": 4, "argv": 2}[change]
        if change in ("contract", "unexpected"):
            assert json.loads(output.getvalue()) == {
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
