"""Original CC0 tiny ONNX towers for contract tests, never a substitute for CLIP quality."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path


def local_embedding_assets(directory: Path, *, family: str = "clip") -> Path:
    """RGB channel means and an explicit color-token table share three dimensions.

    This proves native loading, tensor signatures, tokenization and Space wiring.
    It is deliberately not a pretrained CLIP model or a semantic quality fixture.
    """
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, pre_tokenizers

    directory.mkdir(parents=True, exist_ok=True)
    image = helper.make_model(
        helper.make_graph(
            [
                helper.make_node("GlobalAveragePool", ["pixel_values"], ["mean"]),
                helper.make_node("Flatten", ["mean"], ["image_embeds"], axis=1),
            ],
            "original-rgb-contract",
            [
                helper.make_tensor_value_info(
                    "pixel_values", TensorProto.FLOAT, [1, 3, 32, 32]
                )
            ],
            [helper.make_tensor_value_info("image_embeds", TensorProto.FLOAT, [1, 3])],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
        ir_version=9,
    )
    onnx.save(image, directory / "image.onnx")
    assets = {
        "image.onnx": hashlib.sha256(
            (directory / "image.onnx").read_bytes()
        ).hexdigest()
    }
    manifest = {
        "model_key": "two-tower-contract",
        "model_revision": "original-cc0-v1",
        "family": family,
        "native_dimension": 3,
        "image": {
            "graph": {"filename": "image.onnx", "sha256": assets["image.onnx"]},
            "image_size": 32,
            "mean": [0, 0, 0],
            "std": [1, 1, 1],
            "canary": [1 / math.sqrt(3)] * 3,
        },
    }
    if family == "clip":
        table = numpy_helper.from_array(
            np.asarray(
                [[0, 0, 0], [1, 1, 1], [1, 0, 0], [0, 0, 1], [1, 1, 1]],
                dtype=np.float32,
            ),
            name="table",
        )
        axis = numpy_helper.from_array(np.asarray([1], dtype=np.int64), name="axes")
        text = helper.make_model(
            helper.make_graph(
                [
                    helper.make_node("Gather", ["table", "input_ids"], ["tokens"]),
                    helper.make_node(
                        "ReduceSum", ["tokens", "axes"], ["text_embeds"], keepdims=0
                    ),
                ],
                "original-token-contract",
                [helper.make_tensor_value_info("input_ids", TensorProto.INT64, [1, 8])],
                [
                    helper.make_tensor_value_info(
                        "text_embeds", TensorProto.FLOAT, [1, 3]
                    )
                ],
                [table, axis],
            ),
            opset_imports=[helper.make_opsetid("", 17)],
            ir_version=9,
        )
        onnx.save(text, directory / "text.onnx")
        tokenizer = Tokenizer(
            models.WordLevel(
                {"[PAD]": 0, "[UNK]": 1, "red": 2, "blue": 3, "gray": 4},
                unk_token="[UNK]",
            )
        )
        tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
        tokenizer.save(str(directory / "tokenizer.json"))
        manifest["text"] = {
            "graph": {
                "filename": "text.onnx",
                "sha256": hashlib.sha256(
                    (directory / "text.onnx").read_bytes()
                ).hexdigest(),
            },
            "tokenizer": {
                "filename": "tokenizer.json",
                "sha256": hashlib.sha256(
                    (directory / "tokenizer.json").read_bytes()
                ).hexdigest(),
            },
            "max_tokens": 8,
            "pad_id": 0,
            "pad_token": "[PAD]",
            "canary_text": "gray",
            "canary": [1 / math.sqrt(3)] * 3,
        }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory
