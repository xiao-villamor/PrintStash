# Remote Artifact cache

PrintStash can retain verified copies of managed remote Artifacts on local disk.
Enable the cache in **Settings → Storage → Remote Artifact cache**. It is off by
default. Local Vault files and linked Library sources keep their existing read
paths. Converted STL files and thumbnails are separate representations and are
not cached under the original Artifact's digest.

Canonical delivery checks authorization and conditional requests first. A safe
provider redirect remains preferred over a cache hit and does not populate the
cache. Otherwise a verified local copy can serve the request. Complete proxy
responses may populate the cache; partial Range responses never publish a full
entry. Server-side previews, printing and archive consumers use the same cache
through Artifact content.

Cached files are private and disposable. Every fill verifies the exact recorded
size and SHA-256 before atomic publication. The cache never supplies evidence for
an authoritative Vault integrity audit. A full audit still reads the original
storage and can report source corruption while a valid cached representation
exists. Audits also inspect up to 100 cached entries and report cache-specific
corruption independently of authoritative storage findings.

The index and files share one local filesystem. Active readers hold durable
leases before receiving a path. Clearing marks busy entries for removal after
the last reader closes, so the displayed bytes can remain above zero temporarily.
Disabling stops new cache use; it does not delete stored files. Shrinking limits
schedules idle eviction without interrupting current readers; Settings displays
and refreshes pending reclamation. Cache folder changes
require a process restart. Use a dedicated empty directory; existing unrelated
contents are never enrolled or cleared.

The cache has byte, entry and concurrent-fill limits. Shared capacity admission
also reserves each fill's disk allocation. If the cache is unavailable, ordinary
source streaming remains possible. Filesystem materialization still requires its
own capacity admission; cache fallback cannot bypass disk headroom.

| Environment setting | Default | Meaning |
|---|---|---|
| `VAULT_ARTIFACT_CACHE_ENABLED` | `false` | Enable cache reads and fills |
| `VAULT_ARTIFACT_CACHE_ROOT` | `/data/artifact-cache` | Dedicated private cache directory, on the `/data` volume by default |
| `VAULT_ARTIFACT_CACHE_MAX_BYTES` | `10737418240` | Published and reserved bytes limit |
| `VAULT_ARTIFACT_CACHE_MAX_ENTRIES` | `10000` | Published and reserved entry limit |
| `VAULT_ARTIFACT_CACHE_MAX_FILLS` | `2` | Concurrent fill limit |
| `VAULT_ARTIFACT_CACHE_HEADROOM_BYTES` | `1073741824` | Additional cache filesystem free-space floor |
| `VAULT_ARTIFACT_CACHE_FILL_WAIT_SECONDS` | `30` | Maximum wait for another caller's active fill |
| `VAULT_ARTIFACT_CACHE_VERIFY_EVERY_HITS` | `100` | Rehash sampling interval; zero disables sampling |

Administrator API: `GET`/`PUT /api/v1/config/artifact-cache` reads or replaces the
policy; `DELETE` resets it to environment defaults; `POST .../clear` explicitly
clears idle entries and revokes active fills. Responses report effective root,
restart requirement, policy source, availability, cache bytes, leases, reserved
bytes, fill count and bounded hit/miss/error/eviction counters. Root changes do not
move old cache data; after restart the former dedicated cache directory can be
removed once no process uses it.

[Consumer eligibility and recovery](artifact-cache-design.md) explain the storage
boundary and cache lifecycle.
