# Search generation proposal validation

Generation proposals must choose one provider. Visual profiles require a local paired encoder without text prefix overrides; max aggregation belongs only to multiview. Validate these combinations before generation work is accepted. This change adds assertions against the public schema boundary; it changes no application behavior or coverage floor.

The measured Deep CI reached the backend coverage gate after every ordinary and resource test passed. The proposal module reported83.33%, with unexecuted rejection paths at lines28/34/36. The gate also reported19 other modules; this focused change does not claim to close their gaps or the global gate.

| # | Behaviour (test name in TestGenerationProposal) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | test_preserves_a_single_text_provider | Happy | One remote or local provider | Serialized provider identity retained; semantic text profile | Unit | ✅ |
| 2 | test_preserves_a_local_visual_profile | Happy | Thumbnail, multiview mean/max, point cloud | Serialized local identity, profile and aggregation retained | Unit | ✅ |
| 3 | test_refuses_an_ambiguous_provider_selection | Error | Neither provider or both providers | ValidationError search_one_provider_required | Unit | ✅ |
| 4 | test_refuses_a_visual_provider_without_a_local_paired_contract | Error | Three visual profiles with remote endpoint or explicit query/document prefix | ValidationError search_visual_requires_local_paired_encoder | Unit | ✅ |
| 5 | test_refuses_max_aggregation_outside_multiview | Error | Semantic text, thumbnail or point cloud with max | ValidationError search_aggregation_unavailable | Unit | ✅ |

All cases are in `backend/tests/unit/schemas/test_search_generations.py`. Focused result: **20 passed in2.82s**, one third-party deprecation warning. Ruff/format/whitespace checks pass. No local full, coverage or Deep suite ran; final measured floor validation remains on GitHub after the remaining owner gaps are closed.
