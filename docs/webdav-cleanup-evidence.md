# WebDAV cleanup evidence

PrintStash keeps WebDAV physical deletion and automatic retention disabled.
Conditional requests and quarantine moves passed several useful checks, but the
servers did not demonstrate one portable, durable operation bound to the exact
owned publication. Vault catalog purge can still retain remote bytes. Existing
provider maturity levels are unchanged.

## Reproducible server scope

The contract suite uses Nextcloud `29.0.4-apache` pinned to
`sha256:37d77a1857563d26f7c9a6dc8cdc306ef1118b66f0485bbf457d2f9c1d86e6ed`
and WsgiDAV `4.3.5` from the frozen Python dependency lock. The latter uses its
real filesystem provider, HTTP authentication and lock manager. Each case owns a
random disposable prefix; cleanup never targets an existing user directory.

Run from `backend/` with Docker available:

```sh
./scripts/test.sh full -n 0 -q tests/contract/services/storage_opendal/test_webdav_cleanup.py
```

## Observed results

| Operation / boundary | Nextcloud | WsgiDAV filesystem provider |
|---|---|---|
| DELETE with a stale, different ETag | 412; replacement retained | 412; replacement retained |
| Repeat successful conditional DELETE | 412 on missing key | 404 on missing key |
| MOVE with stale, different ETag and `Overwrite: F` | 412; replacement retained | 412; replacement retained |
| MOVE to occupied quarantine key | 412; both objects retained | 412; both objects retained |
| Reconnect after successful MOVE, then replay after source replacement | Quarantine original and replacement both retained | Quarantine original and replacement both retained |
| Exclusive LOCK | 501, unavailable | Successful; tokenless PUT and DELETE refused with 423 |
| Wrong UNLOCK token | Lock unavailable | Refused |
| Expired lock used for DELETE after replacement | Lock unavailable | 412; replacement retained |
| Equal-size replacement with colliding ETag | No collision observed in these cases | Conditional DELETE removed the replacement; conditional MOVE moved it |
| Same-content overwrite validator | May repeat; both changed and repeated values observed | Repeats with the same timestamp second |
| Production rollback / unverified reclaim after replacement | Refused; replacement retained | Refused; replacement retained |

The initial rapid HTTP writes exposed the WsgiDAV ETag collision without altering
the server. Its filesystem validator combines inode, whole-second modification
time and size. The regression cases hold filesystem timestamps at the same second
to reproduce that boundary deterministically on slow runners. A second case uses
different timestamps to distinguish a validator collision from ignoring
`If-Match`. Same-content overwrites also retain that validator on WsgiDAV;
Nextcloud changed its ETag in a local run and reused it in CI. The conditional
request tests assert the actual validator comparison in either case; neither
outcome supplies a durable publication ownership token.

## Why deletion remains disabled

A conditional request only protects the state described by its validator. HTTP
ETags describe representations; the standard does not make them PrintStash
publication ownership tokens. See [HTTP validator semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.8.3).
The measured WsgiDAV collision makes that distinction observable even for changed
bytes. An ownership ledger plus a prior HEAD cannot close this replacement race.

Locks have a finite lifetime and require the correct token. A recovery attempt
must not assume a pre-crash lock remains active. Nextcloud's tested configuration
does not implement the LOCK operation, and WsgiDAV's expired-lock case confirms
that later cleanup needs fresh evidence. See [WebDAV locks and timeouts](https://www.rfc-editor.org/rfc/rfc4918.html#section-6.6).

The quarantine cases demonstrate lost-client-response recovery and no-overwrite
behavior on these pins. They do not prove a server-crash durability guarantee,
an atomic transaction with PrintStash's ownership journal, or protection of a
quarantine object from replacement before later deletion. The tests deliberately
keep those limits separate from their passing protocol observations.

A future narrowly scoped deletion implementation needs publication-bound identity,
a durable operation journal, recovery at every crash boundary, and conditional
protection of the quarantine object itself. It must prove those properties on
its supported server configuration before advertising an exact deletion
capability. The current adapter continues to refuse cleanup, including after a
matching-size replacement; this evidence does not promote WebDAV to Verified.
