# 3MF geometry capabilities

PrintStash retains every imported Artifact's original bytes. Geometry analysis,
STL viewer conversion and thumbnails are independent derived outputs; refusing
geometry does not prevent downloading the original or using a validated embedded
preview.

## Interpreted geometry

The bounded XML reader supports Core triangle meshes, component assemblies and
build placements. It resolves the primary model through the OPC relationship,
then follows reachable objects and referenced model parts. Unrelated `.model`
parts do not enter measurements. Whole-package archive safety checks still apply
to unreachable entries.

Resource coordinates and affine transforms remain float64. Supported physical
units are micron, millimeter, centimeter, inch, foot and meter; outputs use
millimetres. Nested nonsingular transforms, nonuniform scales and reflections are
supported. Bounds describe vertices referenced by triangles. Legacy slicer
`printable="false"` and `printable="0"` build items are excluded as a compatibility
rule, even though Core 1.4 does not declare that attribute.

The supported Production extension subset is external model-part references
through `p:path`, including referenced component objects and their transforms.
This is **not full Production extension conformance** or a claim of support for
every 3MF extension. Materials, appearance and optional slicer metadata are not
interpreted as additional geometry capabilities. They remain in the original.

## Required capabilities and refusals

Each reached model part's `requiredextensions` prefixes resolve against its XML
namespace declarations. The URI determines the capability; a familiar prefix
bound to an unknown URI is still refused. Core and the supported Production
reference subset are recognized. An unknown required namespace raises a typed
unsupported-capability refusal rather than producing incomplete Core-only
geometry. An undeclared required prefix is invalid source input.

| Output | Unknown required capability |
| --- | --- |
| Mesh metadata | Failed derivative with `failure_reason="unsupported_capability"`; no successful measurement publication |
| Similarity fingerprint, when enabled | Unsupported geometry evidence with `failure_code="unsupported_3mf_capability"`; no usable geometry descriptor |
| On-demand viewer STL | First request accepts preparation; terminal response is HTTP 422 with `detail="unsupported_capability"` |
| Embedded thumbnail | A validated embedded image can still become a ready thumbnail |
| Original download / slicer handoff | Original Artifact bytes remain available through the usual authorization checks |

Refusals for malformed input and exhausted package or geometry budgets remain
separate from unsupported capabilities. Failed outputs retain their terminal
reason for unchanged bytes and recipe; repeated viewer requests do not restart
conversion. Explicit retry or a new recipe can make work eligible again.

The 3MF capability policy introduced interpretation cache version
`geometry-v5-sh5f4577c4`; shared STL validation introduced
`geometry-v6-sh5f4577c4`, followed by the retained-scene eligibility policy.
[Current recipe identities](derivatives.md#retained-3mf-measurements-and-previews)
are recorded with the derivative contracts. Previous fingerprints, candidate
observations and human review decisions remain historical evidence. Candidates interpreted under an earlier version
are stale and cannot
be confirmed or used to create a multipart Model; current-result filters exclude
them. The mathematical fingerprint descriptors and SH basis are unchanged.

## Reader and limits

ZIP entries, decoded bytes, XML documents, reference traversal and expanded faces
remain bounded. Loading and STL conversion run under supervised worker memory and
time limits. A small archive with too many placements can still be refused.
This implementation keeps the existing bounded float64 XML geometry source; it
does not add Lib3MF or silently try an alternative parser.

The [reader decision and frozen pilot](adr/0009-3mf-loader-capabilities.md) explain
why the evaluated Lib3MF distributions were not adopted. Those measurements
describe the historical pilot, not the current reader's performance. See
[derivative behavior](derivatives.md) for demand, publication and retry contracts.

## Behavior coverage matrix

Every row names an implemented assertion. Focused local checks verify changed behaviors; normal PR CI verifies the retained global regressions. Historical pilot measurements are not measurements of this reader.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_preserves_resource_arrays | Happy | tetrahedron Core | float64/int64 arrays owned, identities, exact vertices/faces | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_preserves_resource_arrays` |
| 2 | test_preserves_supported_physical_units | Edge | six units | exact scaled resource positions | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_preserves_supported_physical_units` |
| 3 | test_materializes_nested_mirror_in_physical_units | Edge | inch nested reflection | analytic vertices, corrected faces, prepared equality | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_materializes_nested_mirror_in_physical_units` |
| 4 | test_retains_unique_geometry_without_materialization | Happy | 64 instances one resource | 192 resource bytes,64 placements,no compose/Trimesh | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_retains_unique_geometry_without_materialization` |
| 5 | test_owns_arrays_after_archive_closes | Edge | archive removed after reader | valid compose and extents afterwards | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_owns_arrays_after_archive_closes` |
| 6 | test_preserves_far_resource_precision | Edge | vertices offset1e9 | exact source arrays and10/20/30 extents | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_preserves_far_resource_precision` |
| 7 | test_preserves_expansion_budget_refusal | Error | 3instances8face cap | scene_resource_limit before compose | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_preserves_expansion_budget_refusal` |
| 8 | test_reads_without_native_mesh_import | Happy | subprocess source read | 192bytes, noTrimesh/orchestrator imports | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReadScene::test_reads_without_native_mesh_import` |
| 9 | test_refuses_unknown_required_namespace | Error | unknown/misleadingp/material/mixed URIs | Unsupported3MFCapability with exact namespace | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestRequiredCapabilities::test_refuses_unknown_required_namespace` |
| 10 | test_refuses_an_undeclared_required_prefix | Error | prefix missing from nsmap | invalid_required_extension | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestRequiredCapabilities::test_refuses_an_undeclared_required_prefix` |
| 11 | test_accepts_compatible_namespace_metadata | Edge | optionalunknown orProduction alias | valid scene, unchanged extents | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestRequiredCapabilities::test_accepts_compatible_namespace_metadata` |
| 12 | test_ignores_unreachable_model_parts | Edge | unusedmalformed/requiredunknown part | valid primary resource, bounds | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReachedResources::test_ignores_unreachable_model_parts` |
| 13 | test_does_not_allocate_arrays_for_unreachable_objects | Edge | 2resourcesonly1build | onlysource1converted,192bytes | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReachedResources::test_does_not_allocate_arrays_for_unreachable_objects` |
| 14 | test_validates_a_referenced_external_part | Error | reachedchildbadXML/unknowncap/unboundprefix | exactcauserefusal | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReachedPartValidation::test_validates_a_referenced_external_part` |
| 15 | test_ignores_unused_source_faces_under_the_caller_budget | Edge | reached4faces,unused4,cap4 | reachedmesh admitted | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReachedPartValidation::test_ignores_unused_source_faces_under_the_caller_budget` |
| 16 | test_refuses_reached_geometry_before_array_allocation | Error | reached4facescap3 | resource_limit beforearrays | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReachedPartValidation::test_refuses_reached_geometry_before_array_allocation` |
| 17 | test_preserves_declared_main_over_malformed_unreachable_part | Edge | primaryOPCz-build +malformeddefault | declaredresource identity+4faces | Integration | ✅ `integration/modules/media/test_three_mf_scene.py::TestReachedPartValidation::test_preserves_declared_main_over_malformed_unreachable_part` |
| 18 | test_preserves_unsupported_capability | Error | actualunsupported3MFload | exactGeometryError retained | Integration | ✅ `integration/modules/media/mesh_loading/test_three_mf_outcomes.py::TestLoad3mfMesh::test_preserves_unsupported_capability` |
| 19 | test_preserves_unsupported_capability | Error | actualunsupported3MFconvert | exactGeometryError retained | Integration | ✅ `integration/modules/media/mesh_loading/test_three_mf_outcomes.py::TestToStlBytes::test_preserves_unsupported_capability` |
| 20 | test_retains_the_required_namespace | Happy | unknownrequiredURI | Exact namespace and unsupported_3mf_capability wirecode | Unit | ✅ `unit/modules/media/test_three_mf_scene.py::TestUnsupportedCapability::test_retains_the_required_namespace` |
| 21 | test_rejects_an_invalid_required_namespace | Error | None/empty/bool/int/list/dict | TypeError invalid_capability_namespace | Unit | ✅ `unit/modules/media/test_three_mf_scene.py::TestUnsupportedCapability::test_rejects_an_invalid_required_namespace` |
| 22 | test_refuses_exponential_component_expansion | Error | Exponential graph | scene_resource_limit | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_refuses_exponential_component_expansion` |
| 23 | test_preserves_bounded_component_expansion | Happy | Bounded assembly graph | Expected instance/resource counts | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_preserves_bounded_component_expansion` |
| 24 | test_preserves_plate_multiplicity | Happy | Six instances of one tetrahedron | One resource, six instances, 24 faces and 260 mm X extent | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_preserves_plate_multiplicity` |
| 25 | test_applies_nested_mirror_in_physical_units | Edge | Nested reflection in inch units | Analytic transform and physical bbox | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_applies_nested_mirror_in_physical_units` |
| 26 | test_refuses_cyclic_resource_graph | Error | Referenced cycle | cyclic_resource | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_refuses_cyclic_resource_graph` |
| 27 | test_refuses_dtd_without_reading_external_entity | Error | ExternalDTD/entity model | xml_doctype_forbidden | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_refuses_dtd_without_reading_external_entity` |
| 28 | test_refuses_invalid_placement | Error | Malformed/singular/nonfinite transform | Typed GeometryError | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_refuses_invalid_placement` |
| 29 | test_caps_expanded_faces | Error | Placedfaces exceedcallerbudget | scene_resource_limit | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestThreeMFResources::test_caps_expanded_faces` |
| 30 | test_uses_declared_main_part | Happy | OPC primary part | Declaredmodel resource ID | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestRootRelationship::test_uses_declared_main_part` |
| 31 | test_refuses_unsafe_root_target | Error | Unsafe/external/missing primary target | Expected exact relationship/path code | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestRootRelationship::test_refuses_unsafe_root_target` |
| 32 | test_rejects_invalid_scene_budget | Error | Non-int/out-of-range budget | invalid_scene_budget | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMalformedPackages::test_rejects_invalid_scene_budget` |
| 33 | test_contains_malformed_package | Error | BadZIP/no model/badXML/wrongroot/unsupportedunit | Expected exact GeometryError code | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMalformedPackages::test_contains_malformed_package` |
| 34 | test_refuses_duplicate_archive_entries | Error | DuplicateZIPnames | duplicate_archive_entry | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMalformedPackages::test_refuses_duplicate_archive_entries` |
| 35 | test_refuses_excessive_entry_count | Error | ZIPmembercount beyondlimit | archive_resource_limit | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMalformedPackages::test_refuses_excessive_entry_count` |
| 36 | test_refuses_xml_compression_bomb | Error | Highly compressed model | archive_resource_limit | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMalformedPackages::test_refuses_xml_compression_bomb` |
| 37 | test_preserves_cross_document_resource_identity | Happy | Production reference to internalmodelpart | Resource identity and physical placement | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestProductionResources::test_preserves_cross_document_resource_identity` |
| 38 | test_refuses_unavailable_external_part | Error | Missing/unsafe externalpart | invalid_resource_path | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestProductionResources::test_refuses_unavailable_external_part` |
| 39 | test_reads_the_mesh_exactly_as_written | Happy | Indexed triangle XML withunit | Exact expected vertices andfaces | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMeshAttributeParsing::test_reads_the_mesh_exactly_as_written` |
| 40 | test_accepts_padded_numbers_with_exponents | Edge | Numericpadding/exponents | Exact numeric arrays | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMeshAttributeParsing::test_accepts_padded_numbers_with_exponents` |
| 41 | test_reads_every_object_mesh_of_a_multi_object_package | Happy | Two reachedobjects | Two instances andtwo faces | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMeshAttributeParsing::test_reads_every_object_mesh_of_a_multi_object_package` |
| 42 | test_refuses_a_malformed_attribute_as_an_invalid_package | Error | Missing/nonnumeric coordinates or malformed faceindices | invalid_3mf | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMeshAttributeParsing::test_refuses_a_malformed_attribute_as_an_invalid_package` |
| 43 | test_refuses_a_non_finite_coordinate | Error | NaN sourcecoordinate | nonfinite_geometry | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMeshAttributeParsing::test_refuses_a_non_finite_coordinate` |
| 44 | test_a_mesh_without_triangles_is_refused_as_a_degenerate_surface | Error | Empty triangle set | degenerate_surface | Integration | ✅ `integration/modules/media/test_mesh_resources.py::TestMeshAttributeParsing::test_a_mesh_without_triangles_is_refused_as_a_degenerate_surface` |
| 45 | test_3mf_reserves_an_unknown_count_for_the_bounded_reader | Edge | 3MFXML source | Estimate None instead of bytes/70 | Unit | ✅ `unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_3mf_reserves_an_unknown_count_for_the_bounded_reader` |
| 46 | test_3mf_without_model_part_stays_unknown_for_the_bounded_reader | Edge | Archive unrelatedpayload only | Estimate None, no invented faces | Unit | ✅ `unit/modules/media/mesh_policy/test_estimators.py::TestEstimateTriangleCount::test_3mf_without_model_part_stays_unknown_for_the_bounded_reader` |
| 47 | test_3mf_keeps_the_global_source_byte_ceiling | Error | RawZIP exceeds1MiB configuredcap | exceeds_cap True | Unit | ✅ `unit/modules/media/mesh_policy/test_budgets.py::TestExceedsCap::test_3mf_keeps_the_global_source_byte_ceiling` |
| 48 | test_3mf_with_disabled_byte_cap_still_uses_bounded_scene_admission | Error | Bytecapdisabled reached4facebudget3 | Cheappreflight permitsboundedreader; reader resource_limit | Unit | ✅ `unit/modules/media/mesh_policy/test_budgets.py::TestExceedsCap::test_3mf_with_disabled_byte_cap_still_uses_bounded_scene_admission` |
| 49 | test_to_stl_bytes_refuses_over_cap_mesh | Error | DenseOBJ undercheapfaceguard | No convertedbytes and loader cannotbeentered | Unit | ✅ `unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_refuses_over_cap_mesh` |
| 50 | test_to_stl_bytes_bakes_the_scene_transforms_into_the_geometry | Happy | Placedcomponent3MF | ConvertedSTL physicalbounds include transforms | Unit | ✅ `unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_bakes_the_scene_transforms_into_the_geometry` |
| 51 | test_to_stl_bytes_refuses_a_3mf_whose_placements_exceed_the_budget | Error | Repeated3MF exceedsplacedfacecap | GeometryError scene_resource_limit,no genericTrimeshload | Unit | ✅ `unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_refuses_a_3mf_whose_placements_exceed_the_budget` |
| 52 | test_to_stl_bytes_converts_an_instanced_3mf_inside_the_budget | Happy | 10placements insidebudget | 120convertedfaces,no genericTrimeshload | Unit | ✅ `unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_converts_an_instanced_3mf_inside_the_budget` |
| 53 | test_to_stl_bytes_fails_closed_on_a_3mf_it_cannot_open | Error | CorruptZIP source | GeometryError invalid_3mf | Unit | ✅ `unit/modules/media/mesh_loading/test_exports.py::TestToStlBytes::test_to_stl_bytes_fails_closed_on_a_3mf_it_cannot_open` |
| 54 | test_load_mesh_flattens_scene_with_multiple_geometries | Happy | OBJloadstub returningactual2boxScene | 24materializedfaces throughgenericloader | Unit | ✅ `unit/modules/media/mesh_loading/test_load_mesh.py::TestLoadMesh::test_load_mesh_flattens_scene_with_multiple_geometries` |
| 55 | test_refusal_withdraws_only_unsupported_geometry_authority | Error | Current attempt receives unsupported capability or transient timeout after old dimensions/count/measured volume | Unsupported withdraws geometric scalars and stores typed GEOMETRY_UNAVAILABLE terminal diagnostic; timeout preserves prior facts and schedules retry | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_refusal_withdraws_only_unsupported_geometry_authority` |
| 56 | test_unsupported_geometry_withdrawal_respects_publication_fence | Error | Unsupported reply arrives after cancellation, source replacement, recipe change, or new successful attempt | Old attempt publishes nothing and cannot withdraw prior or replacement geometry authority | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_unsupported_geometry_withdrawal_respects_publication_fence` |
| 57 | test_refuses_required_capability_without_losing_document_preview | Error | Real 3MF requires unknown namespace; with and without document PNG | Geometry refused exact UNSUPPORTED_CAPABILITY; fingerprint UNSUPPORTED exact cause and empty records; original bytes unchanged; embedded PNG survives byte-for-byte when present | Integration | ✅ `integration/modules/media/test_thumbnail_engine.py::TestUnsupported3MFCapability::test_refuses_required_capability_without_losing_document_preview` |
| 58 | test_required_capability_replaces_old_parser_receipts | Edge | Old geometry9/thumbnail8 receipts and READY v4 fingerprint; real required-namespace source with PNG | Geometry10 terminal refusal withdraws authority; thumbnail9 READY embedded; v5 unsupported fingerprint published despite old v4 READY; original retained | Integration | ✅ `integration/modules/derivatives/test_producers.py::TestDeriveMesh::test_required_capability_replaces_old_parser_receipts` |
| 59 | test_unavailable_measurements_preserve_typed_geometry_evidence | Edge | Pure data factory creates absent geometry without loading or workflow dependencies | All five geometry scalars are None and volume is explicitly GEOMETRY_UNAVAILABLE | Unit | ✅ `unit/modules/media/test_mesh_contracts.py::TestMeshMeasurements::test_unavailable_measurements_preserve_typed_geometry_evidence` |
| 60 | test_reopens_viewer_receipts_from_the_previous_scene_recipe | Edge | Demanded 3MF viewer has old recipe1 READY receipt or terminal FAILED receipt at maximum attempts | Current rows exclude old receipt; needed and SQL work source discover viewer2; fresh attempt starts at1 while historical receipt remains unchanged | Integration | ✅ `integration/modules/derivatives/test_records.py::TestNeeded::test_reopens_viewer_receipts_from_the_previous_scene_recipe` |
| 61 | preserves each typed worker failure | Error | FAIL frame for every closed failure enum, including unsupported capability | MeshWorkerError retains the identical enum reason | Unit | ✅ `unit/modules/media/test_stl_isolation.py::TestReplyFrame::test_preserves_a_typed_failure` |
| 62 | rejects malformed worker replies | Error | Unknown failure, non-ASCII payload or malformed size frame | Reports worker_failed instead of trusting corrupted transport | Unit | ✅ `unit/modules/media/test_stl_isolation.py::TestReplyFrame::test_treats_a_malformed_reply_as_a_worker_failure` |
| 63 | reports required extension refusal without writing STL | Error | Real OPC 3MF requires unknown namespace | Unsupported capability reply; no STL output; exact original bytes retained | Integration | ✅ `integration/modules/media/test_stl_worker.py::TestMain::test_reports_required_extension_refusal_without_writing_stl` |
| 64 | preserves required extension refusal across a real worker | Error | Unsupported required extension parsed in supervised child | Parent receives unsupported_capability; source bytes unchanged | Integration | ✅ `integration/modules/media/test_stl_isolation.py::TestToStlBytes::test_preserves_required_extension_refusal_across_a_real_worker` |
| 65 | retains required extension refusal without reprocessing | Error | First authenticated viewer request for unsupported required extension | 202 then durable identical 422 refusals; one attempt; no stored STL; original download exact | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_retains_required_extension_refusal_without_reprocessing` |
| 66 | preserves independent artifacts after required extension refusal | Error | Real upload of unsupported required extension with valid embedded PNG; opt-in similarity enabled through public settings | Upload completed; failed metadata and unsupported fingerprint with typed causes; no usable descriptor; viewer422; original exact; embedded image remains ready after refusal | E2E | ✅ `e2e/test_ingest.py::TestThreeMFCapabilities::test_required_extension_refusal_preserves_independent_artifacts` |
| 67 | serves placed geometry | Happy | Supported component project requested through viewer endpoint | Published STL preserves analytic transformed bounds | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_serves_placed_geometry` |
| 68 | preserves 3MF resource refusal | Error | Expanded scene exceeds configured face budget | Real child reports resource_limit distinctly from unsupported capability | Integration | ✅ `integration/modules/media/test_stl_isolation.py::TestToStlBytes::test_a_3mf_over_the_budget_preserves_resource_refusal` |
| 69 | preserves STEP resource refusal | Error | STEP tessellation exceeds face budget | Reports resource_limit | Integration | ✅ `integration/modules/media/test_stl_isolation.py::TestToStlBytes::test_preserves_step_triangle_refusal` |
| 70 | serves original STL untouched | Happy | Authenticated request for existing STL Artifact | Original bytes delivered without geometry conversion | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_serves_an_stl_untouched` |
| 71 | preserves viewer timeout refusal | Error | Worker reports timeout | Durable422 timeout with one recorded attempt | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_reports_worker_timeout` |
| 72 | rejects unauthorized viewer demand | Error | No authentication | 401 and no persisted demand | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_denies_unauthenticated_preview` |
| 73 | rejects viewer demand outside collection access | Error | Authenticated actor lacks collection access | 403 and no persisted demand | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_denies_preview_without_collection_access` |
| 74 | rejects viewer demand for trashed artifacts | Error | Trashed source | 404 | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_hides_trashed_preview` |
| 75 | serves a published viewer representation while processing disabled | Edge | Ready representation and mesh group disabled | 200 representation remains readable | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_serves_ready_preview_when_disabled` |
| 76 | recovers demand after lost nudge | Edge | Persisted viewer request without delivered nudge | Source finds interactive work for the Artifact | Integration | ✅ `integration/api/v1/files/test_stl.py::TestFileAsStl::test_recovers_demand_after_lost_nudge` |
| 77 | Restores instrumentation of the current reader | Edge | Valid Core mesh or expanded-budget refusal through pilot adapter | Original parser/expander/materializer functions restored after measurement | Integration | ✅ `integration/scripts/test_pilot_lib3mf.py::TestCurrentInstrumentation::test_restores_current_reader_seams` |
| 78 | Preserves placement-budget refusal through the legacy facade | Error | Instanced 3MF exceeds load face budget | Typed scene_resource_limit raised without unbounded Trimesh scene load | Unit | ✅ `unit/modules/media/mesh_processing/test_entry_points.py::TestExtractGeometry::test_extract_geometry_refuses_a_3mf_whose_placements_exceed_the_budget` |
| 79 | Source measurement retains unavailable geometry evidence | Edge | No mesh is available | Dimensions, count and volume remain unknown with GEOMETRY_UNAVAILABLE evidence | Unit | ✅ `unit/modules/media/mesh_measurements/test_measurements.py::TestGeometryFromMesh::test_reports_missing_geometry_volume_evidence` |
| 80 | Interpretation version controls actionable evidence freshness | Edge | Previous v4 or current interpretation candidate and matching READY fingerprints | Previous evidence is stale with no confirmation action; current evidence remains actionable; history is preserved | Integration | ✅ `integration/modules/similarity/candidates/test_candidates.py::TestInterpretationVersion::test_version_controls_current_evidence` |
| 81 | preserves human review history after interpretation changes | Edge | Previously confirmed evidence now belongs to old interpretation | Prior decision, confirmed annotation and historical fingerprints remain; new confirmation rejected as stale without adding a decision | Integration | ✅ `integration/modules/similarity/candidates/test_candidates.py::TestInterpretationVersion::test_version_change_preserves_review_history` |
| 82 | excludes obsolete interpretation from current API results | Edge | Live v4 candidate retains matching v4 fingerprints | HTTP current filter omits candidate; stale filter and unfiltered Model list retain historical evidence without confirmation action | Integration | ✅ `integration/api/v1/test_similarity.py::TestPersistedResults::test_obsolete_interpretation_is_excluded_from_current_results` |
| 83 | preserves embedded preview beyond source byte cap | Edge | 3MF exceeds1MiB raw source ceiling; default embedded preview enabled | No loaded geometry; exact validated PNG retained | Unit | ✅ `unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_over_cap_3mf_still_gets_embedded_preview` |
| 84 | enables embedded preview beyond source byte cap | Edge | 3MF exceeds1MiB; large-file preview flag enabled | No loaded geometry; exact validated PNG retained | Unit | ✅ `unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_large_3mf_uses_embedded_preview_when_flag_on` |
| 85 | disables embedded preview beyond source byte cap | Edge | 3MF exceeds1MiB; large-file preview flag disabled | No geometry or image published | Unit | ✅ `unit/modules/media/thumbnail_engine/test_processing.py::TestAnalyzeMesh::test_large_3mf_skips_embedded_preview_when_flag_off` |
| 86 | discovers geometry from an obsolete metadata recipe | Edge | READY recipe8 metadata with legacy volume evidence | Current recipe is newer; source rediscovers exact Artifact intent | Integration | ✅ `integration/modules/library/test_volume_metadata.py::TestVolumeRecipeRefresh::test_discovers_mesh_metadata_from_previous_evidence_recipe` |
| 87 | preserves native hull evidence at current interpretation | Happy | Real native child reads watertight cube | Current algorithm version; hull_ratio1 remains available | Integration | ✅ `integration/modules/media/test_mesh_isolation.py::TestGeometryMeasurements::test_preserves_native_hull_descriptor` |
| 88 | publishes physical volume evidence with similarity disabled | Happy | Small 3MF or inconsistent winding uploaded through real API | Correct volume state/count/bounds persisted; READY current recipe; no fingerprints | E2E | ✅ `e2e/test_mesh_measurements.py::TestMeshMeasurementEvidence::test_ingestion_publishes_volume_evidence_without_fingerprints` |
| 89 | retains preview when expanded 3MF exceeds budget | Edge | Unknown estimate;20 placements exceed50-face render cap; valid embedded PNG | Embedded strategy and exact preview retained; exact count80/bounds/volume remain ready | Integration | ✅ `integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_instanced_3mf_preserves_measurements_with_embedded_preview` |
| 90 | reports exact 3MF budget refusal without preview | Error | Unknown estimate;20 placements exceed50-face render cap; no embedded preview | No image; preview resource_limit with exact count80/bounds/volume preserved | Integration | ✅ `integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_instanced_3mf_preserves_measurements_when_render_is_refused` |
| 91 | refuses compression bomb before model decompression | Error | Real highly compressed model XML; valid embedded PNG; unknown estimate | PNG read and retained; model member never opened; resource_limit geometry refusal | Integration | ✅ `integration/modules/media/test_mesh_processing.py::TestLoadMesh::test_real_compression_bomb_3mf_is_not_decompressed` |
| 92 | refuses real spatula beyond exact scene face cap | Error | Real repository 3MF with reader cap100 faces | Typed GeometryError with exact resource_limit cause | Integration | ✅ `integration/modules/media/geometry_analysis/test_existing_models.py::TestExistingModels::test_rejects_spatula_when_triangle_budget_is_too_small` |
