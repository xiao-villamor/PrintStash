# Storage transports

A **transport** is the byte protocol behind a storage provider: the local
filesystem, S3, WebDAV, SFTP or an Apache OpenDAL service. Every fact that
differs between transports is declared once, on its descriptor in
`backend/app/modules/storage/transports/`. The catalogue, generated forms and
docs, connection parsing, the remote adapters, the Vault adapter, receipts,
identity and the image build all read those declarations. None of them may
branch on a transport's name: a `kind is TransportKind.X` outside the
`transports` package is a missing descriptor attribute. The one exception is
constructing the built-in Vault adapters (local and native boto3 S3, both
`BuiltinRuntime`), which have their own setup and legacy settings paths.

## Layout

| File | Owns |
| --- | --- |
| `transports/base.py` | `TransportKind`, `StorageUse`, `TransportSpec`, runtimes (`BuiltinRuntime`, `AsyncSSHRuntime`, `OpenDALService`), `CreateStrategy`, `ListingStrategy`, the `Transport` / `RemoteTransport` contract, `ProviderConfig` |
| `transports/<name>.py` | One transport: its `ProviderConfig` subclass and descriptor |
| `transports/__init__.py` | The registry (`TRANSPORTS`) and the `StorageProviderConfig` union |
| `transports/presets.py` | Presets: provider entries reusing an existing transport |
| `transports/compiled.py` | The full-image build check that every registered OpenDAL service compiled |
| `target_identity.py` | `StorageTargetIdentity` and endpoint normalisers (no transport knowledge) |
| `storage_identity.py` | Failure-domain rules; dispatches provider domains to the registry |
| `storage_providers.py` | Facade: parse, resolve, catalogue, forms, generated docs |

## What a descriptor declares

- `resolve(config)`: cross-field validation and the resolved `TransportSpec`.
- `operator_options(spec)`: OpenDAL operator keyword arguments (OpenDAL only).
- `target(options)`, `provider_domain(identity)`: failure-domain identity.
- `provider_ref_fields(...)`: credential-free locator fields hashed into a
  durable provider ref. These hashes are persisted; never change an existing
  transport's fields.
- `vault_runtime` (None = not Vault-capable), `remote_runtime`: drive role
  support, availability, the docs' runtime column and the Docker service list.
- `create_strategy`, `atomic_publication`, `listing`,
  `listed_mtime_whole_seconds`, `provisions_root`, `secrets_select_account`,
  `signed_get`: the safety and protocol facts adapters act on.
- `providers`, `preset_category`, `preset_description`, `large_object_note`,
  `requirements`, `form_defaults`: catalogue and form presentation.

Safety tiers are still measured at runtime by the endpoint probe; a descriptor
states what a transport *can* do, never what an endpoint *has proven*.

## Adding an OpenDAL transport

1. Measure the service: construct `opendal.Operator("<scheme>", ...)` from the
   pinned wheel and read `capability()`. It decides the create strategy
   (`write_with_if_not_exists` → `CONDITIONAL_WRITE`, otherwise a checked
   write) and whether a Vault runtime is defensible (a Vault needs create-only
   publication; without it set `vault_runtime = None`).
2. Write `transports/<name>.py`: the config model (secrets marked
   `secret=True`), the descriptor, and `OpenDALService(scheme, label,
   probe_options)` whose probe options construct without endpoint IO.
3. Add the `TransportKind` member, register the descriptor in `TRANSPORTS` and
   its config in `StorageProviderConfig`.
4. Add the same value to `LibrarySourceKind`, then autogenerate the migration
   that widens its CHECK constraint (see [database.md](database.md)).
5. Run the repo tests; each remaining step fails one of them until done:
   - `tests/repo/test_storage_image.py`: add `services-<scheme>` to the
     Dockerfile's `OPENDAL_SERVICES`.
   - `tests/repo/test_frontend_enums.py`: add the kind to `LibrarySourceKind` in
     `frontend/src/types/config.ts`; `pnpm typecheck` then requires its entry in
     `frontend/src/lib/storage-transports.ts`.
   - `tests/repo/test_storage_provider_docs.py`: regenerate
     `docs/storage-providers.md` from `render_storage_provider_docs()`.
6. Tests: a contract test for the operator options and capabilities against the
   service (or its contract fake), plus the provider-ref and identity rows for
   the new transport. Mark the provider `beta` until validated against a real
   endpoint, and update `docs/provider-support.md` and
   `docs/library-sources.md`.

## Bumping OpenDAL

OpenDAL tags core releases (`v0.59.5`); each publishes one Python distribution
(`0.47.12`, see `bindings/python/Cargo.toml` at the tag). Bump together:
`OPENDAL_SOURCE_VERSION` + `OPENDAL_SOURCE_SHA256` (of the tag tarball) +
`OPENDAL_PYTHON_VERSION` in `backend/Dockerfile`, both pins in
`backend/pyproject.toml`, `uv lock`, and the pairing in
`tests/repo/test_storage_image.py`. The Rust builder image must satisfy the
tag's `rust-version`.
