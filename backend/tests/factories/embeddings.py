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


def onnx_external_tensor_graph(container: str) -> bytes:
    """Hostile tensor locations for the pure graph admission contract."""
    import onnx
    from onnx import TensorProto, helper

    tensor = helper.make_tensor("external", TensorProto.FLOAT, [1], [1])
    tensor.ClearField("float_data")
    tensor.data_location = TensorProto.EXTERNAL
    tensor.external_data.add(key="location", value="/private/must-not-be-read.bin")
    sparse = onnx.SparseTensorProto()
    sparse.values.CopyFrom(tensor)
    sparse.indices.CopyFrom(helper.make_tensor("indices", TensorProto.INT64, [1], [0]))
    sparse.dims.append(1)
    graph = helper.make_graph([], "untrusted", [], [])
    if container == "initializer":
        graph.initializer.append(tensor)
    elif container == "sparse_initializer":
        graph.sparse_initializer.append(sparse)
    else:
        attribute = onnx.AttributeProto(name="untrusted")
        if container in ("graph", "graphs"):
            child = helper.make_graph([], "nested", [], [], [tensor])
            if container == "graph":
                attribute.type = onnx.AttributeProto.GRAPH
                attribute.g.CopyFrom(child)
            else:
                attribute.type = onnx.AttributeProto.GRAPHS
                attribute.graphs.append(child)
        elif container == "tensor":
            attribute.type = onnx.AttributeProto.TENSOR
            attribute.t.CopyFrom(tensor)
        elif container == "tensors":
            attribute.type = onnx.AttributeProto.TENSORS
            attribute.tensors.append(tensor)
        elif container == "sparse_tensor":
            attribute.type = onnx.AttributeProto.SPARSE_TENSOR
            attribute.sparse_tensor.CopyFrom(sparse)
        elif container == "sparse_tensors":
            attribute.type = onnx.AttributeProto.SPARSE_TENSORS
            attribute.sparse_tensors.append(sparse)
        else:
            raise ValueError("unknown_tensor_container")
        node = helper.make_node("Identity", [], [])
        node.attribute.append(attribute)
        graph.node.append(node)
    return helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 17)], ir_version=9
    ).SerializeToString()


def text_embedding_assets(directory: Path, *, pooling: str = "cls") -> Path:
    """Original CC0 token embeddings expose pooling and mask errors explicitly."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, pre_tokenizers

    directory.mkdir(parents=True, exist_ok=True)
    # Padding is deliberately nonzero: mean pooling must apply the mask.
    table = numpy_helper.from_array(
        np.asarray([[0, 10, 0], [1, 1, 1], [1, 0, 0], [0, 0, 1]], dtype=np.float32),
        name="table",
    )
    graph = helper.make_model(
        helper.make_graph(
            [helper.make_node("Gather", ["table", "input_ids"], ["last_hidden_state"])],
            "original-sentence-contract",
            [
                helper.make_tensor_value_info(name, TensorProto.INT64, [1, 8])
                for name in ("input_ids", "attention_mask", "token_type_ids")
            ],
            [
                helper.make_tensor_value_info(
                    "last_hidden_state", TensorProto.FLOAT, [1, 8, 3]
                )
            ],
            [table],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
        ir_version=9,
    )
    onnx.save(graph, directory / "text.onnx")
    tokenizer = Tokenizer(
        models.WordLevel(
            {"[PAD]": 0, "[UNK]": 1, "red": 2, "blue": 3}, unk_token="[UNK]"
        )
    )
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(directory / "tokenizer.json"))
    manifest = {
        "schema_version": 2,
        "model_key": "text-contract",
        "repository": "printstash/original-cc0",
        "model_revision": "1" * 40,
        "native_dimension": 3,
        "language": ["en"],
        "license": "CC0-1.0",
        "text": {
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
            "pooling": pooling,
            "opset": 17,
            "canary_text": "red",
            "canary": [1, 0, 0],
        },
    }
    (directory / "manifest.json").write_text(json.dumps(manifest))
    return directory


def point_embedding_assets(directory: Path) -> Path:
    """Original CC0 three-dimensional grouping contract, not a quality model."""
    import onnx
    from onnx import TensorProto, helper

    from app.modules.inference.manifest import LocalModelManifest, PointModelManifest

    local_embedding_assets(directory)
    paired = LocalModelManifest.model_validate_json(
        (directory / "manifest.json").read_bytes()
    )
    graph = helper.make_model(
        helper.make_graph(
            [
                helper.make_node(
                    "Slice", ["grouped", "starts", "ends", "axes"], ["rgb"]
                ),
                helper.make_node(
                    "ReduceMean", ["rgb"], ["point_embeds"], axes=[2, 3], keepdims=0
                ),
            ],
            "original-point-grouping-contract",
            [
                helper.make_tensor_value_info("centers", TensorProto.FLOAT, [1, 3, 64]),
                helper.make_tensor_value_info(
                    "grouped", TensorProto.FLOAT, [1, 9, 256, 64]
                ),
            ],
            [helper.make_tensor_value_info("point_embeds", TensorProto.FLOAT, [1, 3])],
            [
                helper.make_tensor("starts", TensorProto.INT64, [1], [6]),
                helper.make_tensor("ends", TensorProto.INT64, [1], [9]),
                helper.make_tensor("axes", TensorProto.INT64, [1], [1]),
            ],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
        ir_version=9,
    )
    onnx.save(graph, directory / "points.onnx")
    manifest = PointModelManifest.model_validate(
        {
            "schema_version": 3,
            "model_key": "point-contract",
            "model_revision": "2" * 40,
            "repository": "printstash/original-cc0",
            "checkpoint_sha256": "3" * 64,
            "license": "CC0-1.0",
            "paired": paired.model_dump(),
            "paired_space_hash": paired.space().config_hash,
            "point": {
                "graph": {
                    "filename": "points.onnx",
                    "sha256": hashlib.sha256(
                        (directory / "points.onnx").read_bytes()
                    ).hexdigest(),
                },
                "canary": [1 / math.sqrt(3)] * 3,
            },
        }
    )
    (directory / "manifest.json").write_text(manifest.model_dump_json())
    return directory


def sparse_embedding_assets(directory: Path) -> Path:
    """Original CC0 token-to-logit contract; not a pretrained quality fixture."""
    import numpy as np
    import onnx
    from onnx import TensorProto, helper, numpy_helper
    from tokenizers import Tokenizer, models, pre_tokenizers

    from app.modules.inference.manifest import SparseModelManifest

    directory.mkdir(parents=True, exist_ok=True)
    vocabulary = {
        "[PAD]": 0,
        "[UNK]": 1,
        "bicycle": 2,
        "bike": 3,
        "bracket": 4,
        "mount": 5,
        "lamp": 6,
        "the": 7,
    }
    logits = np.zeros((8, 8), np.float32)
    logits[2, 2], logits[2, 3] = 3, 2
    logits[4, 4], logits[4, 5] = 3, 2
    logits[6, 6] = 3
    graph = helper.make_model(
        helper.make_graph(
            [helper.make_node("Gather", ["logits", "input_ids"], ["output"])],
            "original-sparse-contract",
            [
                helper.make_tensor_value_info(name, TensorProto.INT64, [1, "sequence"])
                for name in ("input_ids", "input_mask", "segment_ids")
            ],
            [
                helper.make_tensor_value_info(
                    "output", TensorProto.FLOAT, [1, "sequence", 8]
                )
            ],
            [numpy_helper.from_array(logits, name="logits")],
        ),
        opset_imports=[helper.make_opsetid("", 17)],
        ir_version=9,
    )
    onnx.save(graph, directory / "model.onnx")
    tokenizer = Tokenizer(models.WordLevel(vocabulary, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    tokenizer.save(str(directory / "tokenizer.json"))
    manifest = SparseModelManifest(
        model_key="sparse-contract",
        model_revision="4" * 40,
        repository="printstash/original-cc0",
        license="CC0-1.0",
        language=("en",),
        graph={
            "filename": "model.onnx",
            "sha256": hashlib.sha256(
                (directory / "model.onnx").read_bytes()
            ).hexdigest(),
        },
        tokenizer={
            "filename": "tokenizer.json",
            "sha256": hashlib.sha256(
                (directory / "tokenizer.json").read_bytes()
            ).hexdigest(),
        },
        vocabulary_size=8,
        opset=17,
        max_tokens=8,
        max_terms=4,
        canary_text="bicycle",
        canary=(
            {"term": "bicycle", "weight": math.log(4)},
            {"term": "bike", "weight": math.log(3)},
        ),
    )
    (directory / "manifest.json").write_text(manifest.model_dump_json())
    return directory
