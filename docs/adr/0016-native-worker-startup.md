# ADR-0016: Reduce unused imports without reusing native parser state

Status: accepted — defer parent-only instruments and database URL parsing at their existing owners; retain fresh guarded children.

## Decision

Keep one disposable native process per Artifact request. Import mesh observations only where the parent records admission, completed supervision or validated phases. An inherited child admission retains the same permit checks and does not construct the parent's Prometheus registry. Import SQLAlchemy's URL parser inside `_sqlite_db_path`, after its existing non-SQLite return; actual directory creation and SQLite URL semantics remain unchanged. Settings validation, worker requests, native libraries and outputs retain their existing contracts. No interpreter pool, parser loop, launcher service, schema, recipe or dependency is added.

The normal mesh request already shares source preparation across metadata, thumbnail and optional fingerprint. Embedding and visual requests already share preparation across their views. Counting one process per view or one source parse per primary output would invent a batching benefit.
