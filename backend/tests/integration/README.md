# `tests/integration` — the default tier

Real SQLite with the production pragmas (`foreign_keys=ON`), real routers, real services,
real storage backend, real RBAC, real fixture files, and every table truncated between
tests. The only things stood in for are outbound boundaries, and only by injection or by
patching `get_http_client` where it is used.

The socket guard makes that structural: a real connection fails the test, so a test that
genuinely needs one is a **contract** test and belongs in `contract/`.

**This is where most tests go.** Every router endpoint, every service that writes the
database, every query over a soft-deletable table, every migration, every provider
normalisation. The fixtures make the Nth test nearly free — `db_session`, `client`,
`auth_headers`, `make_user`, `user_headers`, `headers_for` — which is precisely so that
coverage is cheap and there is no budget to ration.

## The fixtures you will reach for

| Fixture | Gives you |
| --- | --- |
| `db_session` | a session on the shared test engine, rolled back after the test |
| `client` | `TestClient` over the real app |
| `auth_headers` | the suite's superuser, admin scope |
| `user_headers(name)` | a **fresh non-superuser** — the other half of every RBAC contract |
| `make_user` / `headers_for` | when you also need the row (to grant it a role, say) |

`auth_headers` is an admin superuser, which proves nothing about the 403 half of an
endpoint. Every RBAC and scope row needs `user_headers` too.

## Two things to get right

- **Live vs. trashed.** Every read over Models, Files and Documents must have a row
  proving a trashed one is invisible through the read path and visible through
  `scopes.trashed()`.
- **Own the transaction.** `client` runs the app on its own session. Materialise the ids
  you need and `db_session.close()` before the request, or you will be asserting against
  an expired object.
