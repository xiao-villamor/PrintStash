# Library outliner

The sidebar reads one level at a time. Opening a folder requests its first
page of child folders and, when its counts indicate direct content, its first
page of entries. Explicit “Show more folders” and “Show more models” controls
continue those independent lists. The UI requests 50 rows; the API accepts
1–100. Closed folders do not request their entries.

## Read contract

All endpoints require a signed-in user and return `items` and `next_cursor`.
A null cursor ends the list.

- `GET /api/v1/outliner/collections?parent_id=…`: visible children, or visible
  roots when omitted. A grant to a nested folder makes it an accessible root.
  Each folder carries complete `direct_entry_count`, `subtree_entry_count`
  and `visible_child_count`. The envelope also carries
  `parent_direct_entry_count` for unfiled entries at the root.
- `GET /api/v1/outliner/entries?collection_id=…`: a single ordered list of
  lightweight `model` and `multipart` entries directly in the folder, or
  accessible unfiled entries when omitted.
- `GET /api/v1/outliner/search?q=…`: global literal name matches, including
  `collection`, with accessible display paths. Blank search text is rejected.
  “Open location” selects the result’s collection.

The collection endpoint optionally accepts `reveal_id` and returns
`revealed` (a folder or null). The requested node must belong to that same
visible level and satisfy the filters. The client uses the existing ancestry
lookup to reveal a selected path outside downloaded sibling pages, merges by
ID, and keeps ordinary pagination unchanged.

Ordering is `lower(name), kind, id` (kind is constant for folders).
Opaque cursors bind the endpoint, authenticated user, folder, view and
normalized filters. An incompatible or malformed cursor returns
`outliner_cursor_invalid` with HTTP 400. Missing and inaccessible folders
both return 404.

## Views and filters

`all` returns models and multipart sets. `organized` suppresses models
referenced by an accessible multipart set, except in search. `multipart`
returns only sets. `components` returns referenced models once, regardless
of how many sets reference them. Membership is an SQL existence check, never
a downloaded member-ID list.

Canonical artifact, metadata, print-history, printer-presence, tag, favorite
and similarity filters apply to models. Multipart sets retain their own tag
and favorite rules; model-only filters do not become set properties.
Permissions and live-state predicates apply before counting or paging.
Empty folders remain visible without filters; active filters retain branches
with matching descendants.

Visible counters are separate from the canonical unfiltered collection
metadata used for deletion confirmation. Counts include all remaining pages.

## Cache and consistency

TanStack Query caches each scope, folder, view and filter combination using
the application’s 30-second freshness and five-minute retention defaults.
Obsolete requests consume an abort signal. A filter change cannot display the
old response as the current result. Later-page failures retain downloaded
rows and offer a retry; initial failures have an explicit error state.

Library mutations and completed ingests reset outliner pages and counts.
Window-focus revalidation reflects external changes. Pages do not form a
transactional snapshot: concurrent external inserts or renames may change the
next traversal until revalidation.

These additive reads leave `/collections/children`, `/models/outliner`
and `/multipart-models` compatible. The grid, destination pickers and artifact
processing retain their existing contracts. No schema migration is required.

Validation is recorded in [outliner-validation.md](outliner-validation.md).
