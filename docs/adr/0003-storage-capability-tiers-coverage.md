# ADR-0003 implementation coverage

This matrix is the executable acceptance record for ADR-0003 / issue #90.
Paths name the smallest test layer which owns each behaviour. A checked row is
still subject to the repository gates; it does not mean a test may be skipped.

| # | Behaviour | Tier | Evidence | Status |
|---:|---|---|---|:---:|
| 1 | Derive tier from capability axes | Unit | `unit/services/test_storage_backend.py` | ✅ |
| 2 | Render warnings for absent safety axes | Unit | `unit/services/test_storage_backend.py` | ✅ |
| 3 | Probe local roots as Verified | Integration | `integration/services/test_storage_backend.py` | ✅ |
| 4 | Downgrade a hardlink failure | Integration | `integration/services/test_storage_backend.py` | ✅ |
| 5 | Distrust network inode identity | Integration | `integration/services/test_storage_backend.py` | ✅ |
| 6 | Report directory-fsync failure diagnostically | Integration | `integration/services/test_storage_backend.py` | ✅ |
| 7 | Choose weaker local-root result | Integration | `integration/services/test_storage_backend.py` | ✅ |
| 8 | Derive S3 tier from versioning | Integration | `integration/services/test_storage_backend.py` | ✅ |
| 9 | Expose active capabilities through health | Integration | `integration/api/v1/health/test_health.py` | ✅ |
| 10 | Report a probed tier end to end | E2E | `e2e/test_e2e_webdav_storage.py` | ✅ |
| 11 | Upgrade existing ownership rows | Integration | `integration/db/test_migrations.py` | ✅ |
| 12 | Reserve before publication | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 13 | Reject duplicate reservation | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 14 | Retain intent after storage failure | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 15 | Commit ownership with domain row | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 16 | Preserve pending intent after domain rollback | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 17 | Publish every managed key kind through ledger | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 18 | Ignore fresh pending reservations | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 19 | Clear orphan whose object is absent | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 20 | Reclaim matching orphan | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 21 | Block mismatched orphan | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 22 | Hash small orphan without ETag | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 23 | Preserve large orphan without proof | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 24 | Retry transient sweep failure | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 25 | Never sweep committed ownership | Integration | `integration/services/test_storage_publication.py` | ✅ |
| 26 | Record upload ownership end to end | E2E | `e2e/test_e2e_webdav_storage.py` | ✅ |
| 27 | Fail when S3 bucket is absent | Contract | `contract/services/test_storage_backend.py` | ✅ |
| 28 | Never create an S3 bucket | Contract | `contract/services/test_storage_backend.py` | ✅ |
| 29 | Never change S3 lifecycle policy | Contract | `contract/services/test_storage_backend.py` | ✅ |
| 30 | Retain destructive lifecycle findings | Contract | `contract/services/test_storage_backend.py` | ✅ |
| 31 | Preserve backup S3 behaviour | Contract | `contract/services/backup/test_s3.py` | ✅ |
| 32 | Reject unacknowledged Unguarded startup | Integration | `integration/db/models/test_lifespan_starts_background_tasks_and_shuts_down_cleanly.py` | ✅ |
| 33 | Accept acknowledged Unguarded startup | Integration | `integration/db/models/test_lifespan_starts_background_tasks_and_shuts_down_cleanly.py` | ✅ |
| 34 | Warn without blocking Guarded startup | Integration | `integration/db/models/test_lifespan_starts_background_tasks_and_shuts_down_cleanly.py` | ✅ |
| 35 | Reject unconfirmed non-Verified purge | Integration | `integration/api/v1/models/test_purge.py` | ✅ |
| 36 | Permit confirmed non-Verified purge | Integration | `integration/api/v1/models/test_purge.py` | ✅ |
| 37 | Skip scheduled non-Verified purge | Integration | `integration/services/test_storage_deletion.py` | ✅ |
| 38 | Leave Verified purge unchanged | Integration | `integration/api/v1/models/test_purge.py` | ✅ |
| 39 | Enforce purge gate end to end | E2E | `e2e/test_e2e_webdav_storage.py` | ✅ |
| 40 | Serve provider metadata during setup | Integration | `integration/services/test_storage_providers.py` | ✅ |
| 41 | Resolve named S3 presets | Unit | `integration/services/test_storage_providers.py` | ✅ |
| 42 | Resolve Nextcloud WebDAV path | Unit | `integration/services/test_storage_providers.py` | ✅ |
| 43 | Reject invalid provider roots | Unit | `integration/services/test_storage_providers.py` | ✅ |
| 44 | Reject invalid SFTP authentication | Unit | `integration/services/test_storage_providers.py` | ✅ |
| 45 | Apply configuration precedence | Integration | `integration/services/runtime_config/test_runtime_config.py` | ✅ |
| 46 | Reject mixed legacy provider input | Integration | `integration/api/v1/test_config.py` | ✅ |
| 47 | Mask stored provider secrets | Integration | `integration/api/v1/test_config.py` | ✅ |
| 48 | Deny provider updates to non-superusers | Integration | `integration/api/v1/test_config.py` | ✅ |
| 49 | Report optional transports unavailable | Unit | `integration/services/test_storage_providers.py` | ✅ |
| 50 | Persist provider setup end to end | Playwright real | `e2e-real/storage/storage-provider.spec.ts` | ✅ |
| 51 | Stream WebDAV create/read round trip | Contract | `contract/services/test_storage_opendal.py` | ✅ |
| 52 | Stream SFTP password round trip | Contract | `contract/services/test_storage_opendal.py` | ✅ |
| 53 | Stream SFTP mounted-key round trip | Contract | `contract/services/test_storage_opendal.py` | ✅ |
| 54 | Remove failed remote temporary key | Contract | `contract/services/test_storage_opendal.py` | ✅ |
| 55 | Record post-write remote evidence | Contract | `contract/services/test_storage_opendal.py` | ✅ |
| 56 | Fail closed for remote verified mutation | Contract | `contract/services/test_storage_opendal.py` | ✅ |
| 57 | Upload artifact through WebDAV | E2E | `e2e/test_e2e_webdav_storage.py` | ✅ |
| 58 | Filter providers by category | Frontend unit | `components/__tests__/storage-provider-picker.test.tsx` | ✅ |
| 59 | Show tier before provider fields | Frontend unit | `components/__tests__/storage-provider-picker.test.tsx` | ✅ |
| 60 | Disable unavailable providers | Frontend unit | `components/__tests__/storage-provider-picker.test.tsx` | ✅ |
| 61 | Render secrets as write-only fields | Frontend unit | `components/__tests__/storage-provider-picker.test.tsx` | ✅ |
| 62 | Configure remote provider in browser | Playwright real | `e2e-real/storage/storage-provider.spec.ts` | ✅ |
| 63 | Detect provider-documentation drift | Repo | `repo/test_storage_provider_docs.py` | ✅ |

Summary: **63 implemented, 0 missing, 0 skipped**.
