# Outliner pagination validation

Original acceptance rows 1–37 are retained. Unqualified backend test names refer to `backend/tests/integration/api/v1/test_outliner.py::TestOutliner`. Frontend `sidebar` refers to `frontend/src/components/__tests__/filter-sidebar.test.tsx`; `queries/outliner` and `query-client` refer to files beneath `frontend/src/lib/__tests__/`. Real browser tests are in `frontend/tests/e2e-real/`; `vault.spec.ts` is in `frontend/tests/e2e/`.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | Reach beyond 500 | Happy | >500 models | Target accessible | Integration | ✅ `test_reaches_a_model_beyond_the_former_global_limit` |
| 2 | Traverse folder entries | Happy | 503 models | Each appears once | Integration | ✅ `test_pages_through_every_entry` |
| 3 | Traverse unfiled entries | Edge | 503 root models | All permitted entries reachable | Integration | ✅ `test_pages_through_every_entry` |
| 4 | Traverse multipart pages | Happy | 503 sets | Each appears once | Integration | ✅ `test_pages_through_every_entry` |
| 5 | Stable tie-break | Edge | Equal mixed names | Kind then ID | Integration | ✅ `test_orders_equal_names_by_kind_then_id` |
| 6 | Detect page end | Edge | 0, 1, 49, 50, 51 entries | Correct continuation | Integration | ✅ `test_marks_the_last_page` |
| 7 | Reject invalid pagination | Error | Invalid limit/cursor | 400/422 | Integration | ✅ `test_rejects_invalid_parameters`; `test_rejects_malformed_cursor` |
| 8 | Reject incompatible cursor | Error | Different user/scope/folder/view/filter | 400 | Integration | ✅ `test_rejects_cursor_from_another_query`; `test_rejects_cursor_from_another_user`; `test_rejects_cursor_from_another_endpoint` |
| 9 | Exclude trash | Edge | Trashed models/folders | Omitted from results/counts | Integration | ✅ `test_hides_trashed_models`; `test_hides_entries_in_trashed_collections` |
| 10 | Hide inaccessible folders | Error | Missing or forbidden | Same 404 | Integration | ✅ `test_hides_an_inaccessible_folder` |
| 11 | Require authentication | Error | Anonymous to all endpoints | 401 | Integration | ✅ `test_requires_authentication` |
| 12 | Restrict printer filters | Error | Non-admin | 403 | Integration | ✅ `test_restricts_printer_filters` |
| 13 | Preserve partial roots | Edge | Only nested grant | Accessible root/label | Integration | ✅ `test_preserves_granted_roots` |
| 14 | Count all descendants | Happy | Unloaded pages | Complete totals | Integration | ✅ `test_counts_the_complete_branch` |
| 15 | Keep matching ancestry | Edge | Deep filtered match | Ancestors retained | Integration | ✅ `test_keeps_a_filtered_descendant_branch` |
| 16 | Apply views before paging | Happy | Four views; >500 sets | Correct membership | Integration | ✅ `test_applies_the_view_before_pagination`; `test_uses_membership_beyond_500_multipart_sets` |
| 17 | Hide private grouping | Edge | Private set, public member | Member remains visible | Integration | ✅ `test_does_not_hide_members_of_private_groups` |
| 18 | Apply filter families | Happy | Artifact, metadata, history, printer, tags, favorites | Matching entries | Integration | ✅ `test_applies_artifact_filter_families`; `test_applies_print_history_filter_families`; `test_applies_user_specific_filters`; `test_keeps_multipart_filters_distinct` |
| 19 | Search unopened folders | Happy | Deep match | Global hit with path | Integration | ✅ `test_search_returns_a_deep_uncached_location` |
| 20 | Search grouped models | Edge | Organized member | Matching model returned | Integration | ✅ `test_search_reveals_a_grouped_model` |
| 21 | Search punctuation literally | Edge | Percent/underscore | Only literal matches | Integration | ✅ `test_searches_literal_names` |
| 22 | Expand unloaded content | Happy | Positive metadata | Arrow before entry request | Frontend | ✅ `sidebar: reuses pages of the opened branch after reopening` |
| 23 | Load opened branches | Happy | Closed siblings | Only opened folder fetched | Frontend | ✅ `sidebar: asks for a folder's children only once it is opened` |
| 24 | Reuse cached pages | Happy | Close/reopen | No new request | Frontend | ✅ `sidebar: reuses pages of the opened branch after reopening` |
| 25 | Isolate old filters | Edge | Late response | Aborted old request; current rows | Frontend | ✅ `queries/outliner: isolates late responses from obsolete filters` |
| 26 | Retry continuation | Error | Second-page failure | Keep rows; append retry | Frontend | ✅ `sidebar: recovers a failed continuation without losing loaded rows` |
| 27 | Report initial failure | Error | First-page failure | Error and retry | Frontend | ✅ `sidebar: shows an initial failure with retry instead of pretending the branch is empty` |
| 28 | Reset after mutations | Happy | Mutation paths/ingest | Discard continuation pages | Frontend | ✅ `query-client: outliner mutation refresh` |
| 29 | Restore search navigation | Happy | Escape | Original expansion/selection | Frontend | ✅ `sidebar: restores the expanded tree after Escape from global search` |
| 30 | Reveal later sibling | Edge | Selected off-page node | No page walk or duplicate | Frontend | ✅ `sidebar: reveals a selected location beyond the first sibling page without walking previous pages` |
| 31 | Keyboard pagination | Happy | Enter on Show more | Stable focus at end | Real browser | ✅ `outliner-pagination.spec.ts: moves a model reached through keyboard pagination beyond 500 entries` |
| 32 | Open a late model | Happy | 502 entries | Detail route reached | Real browser | ✅ `outliner-pagination.spec.ts: moves a model reached through keyboard pagination beyond 500 entries` |
| 33 | Move a late model | Happy | Drag final-page model | Destination persisted | Real browser | ✅ `outliner-pagination.spec.ts: moves a model reached through keyboard pagination beyond 500 entries` |
| 34 | PostgreSQL compatibility | Edge | Mixed rows/filters/grants | Same sort/count/access | Integration | ✅ `postgres/test_collection_tree.py::TestOutlinerOnPostgres` |
| 35 | Bound SQL shape | Edge | 10x library | Constant statements/parameters | Repo | ✅ `test_read_scaling.py::TestLibraryReads` |
| 36 | Meet performance budget | Edge | 25k folders/100k models | New reads <=0.5s | Scale | ❌ pending measurement |
| 37 | Preserve old contracts | Edge | Existing endpoints | Compatible schema | Integration/repo | ✅ `test_preserves_legacy_outliner_shape`; `test_openapi_contract.py` |
| 38 | Cursor API journey | Happy | Write then page | All created entries returned | E2E | ✅ `backend/tests/e2e/test_collection_tree.py::TestBrowseCollectionTree::test_pages_every_root_entry` |
| 39 | Avoid hidden desktop reads | Edge | Mobile viewport | No outliner HTTP | Browser | ✅ `vault.spec.ts: mobile vault skips the desktop outliner request` |
