# Preplaced CLIP integration fixture

`clip-vit-base-patch32-int8.json` describes the actual two-tower
[Xenova CLIP ViT-B/32 ONNX export](https://huggingface.co/Xenova/clip-vit-base-patch32/tree/d15189d7028b43f1d3e65039190477f6af591c2a).
Upstream revision: `d15189d7028b43f1d3e65039190477f6af591c2a`.
OpenAI's CLIP project publishes its model under the
[MIT license](https://github.com/openai/CLIP/blob/main/LICENSE).
This repository checks in the configuration and expected canaries, not the weights.

The two quantized towers retain 512 native output dimensions. They are real
pretrained weights. Tests using `tests.factories.embeddings.local_embedding_assets`
are separately identified original CC0 protocol fixtures, not semantic models.

| File | Upstream path | Bytes | SHA-256 |
|---|---|---:|---|
| `vision_model_quantized.onnx` | `onnx/vision_model_quantized.onnx` | 89,117,001 | `583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299` |
| `text_model_quantized.onnx` | `onnx/text_model_quantized.onnx` | 64,504,507 | `73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a` |
| `tokenizer.json` | `tokenizer.json` | 2,224,119 | `f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd` |

Place the files in one directory and copy this fixture there as `manifest.json`.
For local validation from `backend/`:

```bash
SIMILARITY_CLIP_ASSETS=/absolute/path/to/assets uv run pytest \
  tests/integration/modules/inference/preplaced_clip.py -q
```

The optional CI `native_clip` dispatch runs this same preplaced-model lane on
amd64 and arm64. Ordinary PR checks use the small original native contract models
and do not fetch pretrained weights.

Canaries were calculated independently with CPU ONNX Runtime using a uniform
127/255 gray image and the declared text. The contract tests also query the real
text tower against red and blue image outputs and verify authorized local search
with native 2,048-byte vector BLOBs. This verifies integration and modality
compatibility, not broad semantic retrieval quality.
