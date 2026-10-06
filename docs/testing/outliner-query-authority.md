# Outliner query authority

Sidebar endpoints must preserve printer administration authority and reject contradictory query scopes with explicit errors. Tests drive actual authenticated HTTP requests with factory users, printers and collections; no route/service logic is mocked.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | `denies_printer_id_to_nonadministrator` | Error | Real printer ID, viewer, each endpoint | 403 admin_required | Integration | ✅ `integration/api/v1/test_outliner.py::TestOutlinerQueryAuthority::test_denies_printer_id_to_nonadministrator` |
| 2 | `rejects_legacy_collection_selectors` | Error | Actual collection path or direct=true, each endpoint | 422 outliner_use_collection_id | Integration | ✅ `integration/api/v1/test_outliner.py::TestOutlinerQueryAuthority::test_rejects_legacy_collection_selectors` |
| 3 | `rejects_search_text_on_listing_endpoints` | Error | q=Needle on collections/entries | 422 outliner_use_search | Integration | ✅ `integration/api/v1/test_outliner.py::TestOutlinerQueryAuthority::test_rejects_search_text_on_listing_endpoints` |
| 4 | `rejects_scoped_global_search` | Error | Real parent/reveal ID with q | 422 outliner_search_is_global | Integration | ✅ `integration/api/v1/test_outliner.py::TestOutlinerQueryAuthority::test_rejects_scoped_global_search` |
| 5 | `requires_parent_selector_for_collection_listing` | Error | Actual collection_id on collections | 422 outliner_use_parent_id | Integration | ✅ `integration/api/v1/test_outliner.py::TestOutlinerQueryAuthority::test_requires_parent_selector_for_collection_listing` |
| 6 | `requires_collection_selector_for_entry_listing` | Error | Actual parent/reveal ID on entries | 422 outliner_use_collection_id | Integration | ✅ `integration/api/v1/test_outliner.py::TestOutlinerQueryAuthority::test_requires_collection_selector_for_entry_listing` |

Each negative case asserts both the exact HTTP status and semantic error detail through the real router. Existing pagination, visibility, accepted printer filtering and search tests remain in the affected file. No public endpoint/schema/security policy changes. Measured coverage remains pending final GitHub Deep CI.
