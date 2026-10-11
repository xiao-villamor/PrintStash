# Lossless thumbnail encoding evidence

Mesh thumbnail recipe 14 uses profile version 3: WebP `lossless=True`,
`exact=True`, `method=2`, `quality=80`. The previous method was 6 with Pillow's
implicit quality 80. Render, visual-view and compute identities are bumped
together with the native-triangle renderer; exact geometry recipe 12 and paired
encoder alignment do not change.

## Compression-only experiment

[Sanitized aggregate evidence](compute-encoding-200.json) records a deterministic
stratified selection of 200 existing 640×480 cached thumbnails: 100 OBJ, 93 STL
and 7 STEP. Each method received the same decoded RGBA bytes; the method order
rotated between images. One warmup per method preceded the timed cohort.
Python 3.14.8, Pillow 12.3.0 and libwebp 1.6.0 were used. No source was parsed or
rendered, no ingestion was repeated, and no R2 requests were issued.

| Method | Sum of encoding monotonic durations | Median | p95 | Output bytes | Exact RGBA encodes |
| --- | ---: | ---: | ---: | ---: | ---: |
| 6 | 47.588159 s | 261.242 ms | 327.521 ms | 7,906,064 | 200/200 |
| 2 | 18.784409 s | 96.597 ms | 123.572 ms | 8,115,260 | 200/200 |
| 0 | 2.696142 s | 12.665 ms | 24.010 ms | 16,605,034 | 200/200 |

Method 2 saved 28.804 summed encoding seconds and added 209,196 bytes (2.65%).
Its monotonic ratio was 2.53× and it was faster on 192/200 paired inputs. Method
0 was faster but produced 110.03% more bytes, so method 2 is the implemented
tradeoff. These are compression measurements, not whole-pipeline improvement.
Each format cohort is descriptive and individually smaller than 200.

Only `Image.save` was timed; disk reads, decode and verification were excluded.
UTC and monotonic duration differ by 3.270039 s across the complete experiment.
Raw UTC, monotonic and process-CPU clocks are retained separately. The ratios
above use the raw monotonic counter, not an externally calibrated wall clock.
The prior post-geometry stage also includes GPU waiting, preparation and other
work; its duration cannot be attributed entirely to WebP compression.

## Behavior coverage

Paths prefixed `core/` refer to `backend/packages/printstash-core/tests/`. The focused serial run passed 224 core tests and 83 backend tests on 2026-10-11.
Repository test hygiene passed 5,200 cases; Ruff and generated-profile checks passed.
Historical rendering pixel hashes remain unchanged. The frozen visual-input
fixture `media/visual-prepared-v1.json` changes only its `view_identity` for
`canonical-views-media-thumbnail-v5` and the explicit quality-80 profile. Its
source digest, thumbnail RGB digest and six view RGB digests are retained; the
identity assertion and those original pixel assertions passed together in the
409-test serial recheck on 2026-10-11; none of the seven RGB hashes changed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| E1 | `core/mesh/test_preview_profile.py::TestPreviewProfile::test_canonical_thumbnail_profile_is_stable` | Happy | Profile v3 | Shared method 2 / quality 80 policy with existing camera values | Unit | ✅ passed |
| E2 | `core/mesh/test_preview_profile.py::TestPreviewProfile::test_generated_frontend_profile_matches_manifest` | Happy | Generated frontend profile | Manifest and browser constants agree | Unit | ✅ passed |
| E3 | `core/mesh/test_preview_profile.py::TestPreviewProfile::test_profile_structure_rejects_invalid_recipe` | Error | Out-of-range method/quality or boolean quality | Invalid profile is rejected | Unit | ✅ passed |
| E4 | `core/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_exact_decoded_rgba` | Happy | PNG/WebP including alpha 0 and partial alpha | Every decoded RGBA byte matches | Unit | ✅ passed |
| E5 | `core/mesh/test_rasterizer.py::TestEncodeRenderedPixels::test_preserves_rgba_across_compression_effort_change` | Happy | Spatially varying RGBA, old and current effort | Both decoded images preserve all input bytes | Unit | ✅ passed |
| E6 | `backend/tests/unit/modules/media/test_thumbnail.py::TestWebpNormalization::test_canonical_renderer_webp_is_validated_without_reencoding` | Edge | Canonical method 2 or method 6 WebP | Validated encoded bytes are reused | Unit | ✅ passed |
| E7 | `backend/tests/integration/modules/derivatives/test_source.py::TestPending::test_rederives_thumbnails_from_the_previous_gpu_recipe` | Happy | Ready or exhausted-failed recipe 13 thumbnail | Artifact becomes eligible for current derivation | Integration | ✅ passed |
| E8 | `backend/tests/integration/modules/derivatives/test_source.py::TestPending::test_keeps_previous_thumbnail_visible_during_recipe_backfill` | Edge | Old ready thumbnail with current metadata | Backfill preserves old pointer and exact metadata | Integration | ✅ passed |
| E9 | `core/search/test_visual_inputs.py::TestVisualRecipe::test_refuses_views_from_before_native_triangle_rendering` | Error | Previous thumbnail or multiview identity | Prior rendered views cannot masquerade as current inputs | Unit | ✅ passed |
| E10 | `backend/tests/unit/modules/inference/test_manifest.py::TestLocalModelManifest::test_invalidates_vectors_from_the_previous_portable_renderer` | Happy | Identical model, old raster recipe | Changed render produces a distinct derived vector identity | Unit | ✅ passed |
| E11 | `backend/tests/unit/modules/inference/test_manifest.py::TestEncoderAlignment::test_renderer_changes_only_derived_identity` | Happy | Existing pinned paired encoder assets | Rendering revisions preserve native alignment | Unit | ✅ passed |
| E12 | Compression profile, `compute-encoding-200.json` | Happy | Same 200 cached frames, three methods | 600/600 exact RGBA encodes; sizes and clock domains recorded | Diagnostic | ✅ measured |
| E13 | `backend/tests/integration/modules/media/geometry_analysis/test_geometry_analysis.py::TestEmbeddingViews::test_preserves_frozen_visual_input_hashes` | Happy | Existing frozen source and pixel digests with explicit v5 identity | Current recipe identity changes while all seven canonical RGB hashes remain unchanged | Integration | ✅ passed |
