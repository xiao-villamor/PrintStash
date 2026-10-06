# Embedded preview boundaries

3MF preview selection must enforce archive and image budgets before publication, reject unsafe members, and fall back only to a valid unambiguous candidate. Tests use real ZIP bytes and Pillow images. No budget constants are changed.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `archive_entry_ceiling` | Error | 4097 actual members | No preview returned | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_archive_entry_ceiling` |
| 2 | `unsafe_member_name` | Error | Absolute path or UTF-8 name >1024 bytes | No preview returned | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_unsafe_member_name` |
| 3 | `thumbnail_candidate_ceiling` | Error | 65 actual candidate members | No preview returned | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_thumbnail_candidate_ceiling` |
| 4 | `actual_compression_ratio_refusal` | Error | Image expands >200x | Image refused | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_actual_compression_ratio_refusal` |
| 5 | `actual_single_image_ceiling` | Edge | 32MiB+1 canonical candidate, valid fallback | Valid fallback selected, bytes unchanged | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_actual_single_image_ceiling` |
| 6 | `actual_aggregate_ceiling` | Edge | Two 32MiB invalid candidates, valid third | Third not inflated after 64MiB consumed | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_actual_aggregate_ceiling` |
| 7 | `corrupt_candidate_fallback` | Error | Bad CRC canonical candidate, valid plate | Valid fallback selected | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_corrupt_candidate_fallback` |
| 8 | `oversized_pixel_header` | Error | IHDR >25000000 pixels | No preview returned | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_oversized_pixel_header` |
| 9 | `strict_png_validation` | Error | Short header or undecodable image | Strict path refuses payload | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_strict_png_validation` |
| 10 | `semantic_fallback_rank` | Happy | Nested thumbnail or plate_01 vs generic | Semantically preferred bytes selected | Unit | ✅ `unit/modules/media/mesh_previews/test_embedded_thumbnail.py::TestEmbeddedPreviewBoundaries::test_semantic_fallback_rank` |

The 32MiB+1 candidate is a Pillow-decodable PNG with trailing padding; its smaller valid fallback proves size refusal independently of image corruption. The aggregate case consumes two actual 32MiB invalid candidates before the otherwise valid third candidate. These are extractor contracts, not global publication or RSS qualification. Final module coverage is pending GitHub Deep CI.
