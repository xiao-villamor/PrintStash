# Preplaced CLIP integration fixture

`clip-vit-base-patch32-fp32.json` describes the actual two-tower
[Xenova CLIP ViT-B/32 ONNX export](https://huggingface.co/Xenova/clip-vit-base-patch32/tree/d15189d7028b43f1d3e65039190477f6af591c2a).
Upstream revision: `d15189d7028b43f1d3e65039190477f6af591c2a`.
OpenAI's CLIP project publishes its model under the
[MIT license](https://github.com/openai/CLIP/blob/main/LICENSE).
This repository checks in the configuration and expected canaries, not the weights.

The two float32 towers retain 512 native output dimensions. They are real
pretrained weights. Tests using `tests.factories.embeddings.local_embedding_assets`
are separately identified original CC0 protocol fixtures, not semantic models.

| File | Upstream path | Bytes | SHA-256 |
|---|---|---:|---|
| `vision_model.onnx` | `onnx/vision_model.onnx` | 351,685,709 | `fd6e1402a588279d1723c7534d4bcba5bc0b14b47dfab0e46f8c47b8270d7d40` |
| `text_model.onnx` | `onnx/text_model.onnx` | 254,058,553 | `3f6571f5bad13a97c469c1622e1cfc4d9aef78b79fdbfcff804ca357bfada8cc` |
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

The initial dynamically quantized export failed the fixed canary on both native
CI architectures despite matching asset hashes: maximum image drift was 0.03194
on amd64 and 0.01492 on arm64 compared with the local SSE CPU; text drift was
0.00469 and 0.00391. The reference fixture therefore uses the upstream float32
export. The canary tolerance remains 0.0001; incompatible outputs still fail
closed. Changing the manifest changes its embedding Space identity. The two
float32 graphs occupy approximately 606 MB on disk, so this example has a larger
memory requirement than the original small ONNX protocol fixtures.
