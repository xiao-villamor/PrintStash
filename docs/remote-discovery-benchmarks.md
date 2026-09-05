# Remote directory discovery benchmarks

Measured on 2026-09-05 using production source factories, native protocol clients
and separate-process protocol servers generating directories without file bodies.
SQLite uses the production pragmas on disk. Each processing page constructs a new
source adapter, proving that completed inventory pages do not reread the directory.
The contract suite asserts exact key coverage, pages of at most 1,000 entries and
less than 64 MiB of peak RSS growth.

| Transport | Entries | Pages | Seconds | Requests | Connections | Wire bytes | Peak RSS MiB | RSS growth MiB | Inventory MiB |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| s3 | 1,000 | 1 | 0.46 | 1 | 1 | 143,137 | 207.89 | 13.73 | 5.33 |
| s3 | 10,000 | 10 | 3.40 | 10 | 1 | 1,431,820 | 218.64 | 0.14 | 9.50 |
| s3 | 100,000 | 100 | 55.70 | 100 | 1 | 14,318,740 | 217.22 | 5.93 | 51.00 |
| sftp | 1,000 | 1 | 0.46 | 9 | 1 | 109,617 | 223.55 | 4.54 | 5.33 |
| sftp | 10,000 | 10 | 4.71 | 80 | 1 | 1,051,865 | 225.03 | 0.20 | 10.00 |
| sftp | 100,000 | 100 | 49.24 | 783 | 1 | 10,473,729 | 224.83 | 2.28 | 47.35 |
| webdav | 1,000 | 1 | 0.36 | 1 | 1 | 310,302 | 209.45 | 1.55 | 5.33 |
| webdav | 10,000 | 10 | 4.62 | 1 | 1 | 3,100,302 | 211.29 | 1.84 | 9.50 |
| webdav | 100,000 | 100 | 58.06 | 1 | 1 | 31,000,302 | 220.15 | 2.98 | 51.00 |

Requests count S3 list requests, WebDAV PROPFIND requests, or SFTP READDIR
requests. Wire bytes count XML response bodies for HTTP and SSH transport frames
for SFTP, so cross-protocol byte counts have different framing. Inventory storage
includes the SQLite database, WAL and shared-memory files. Content temporary
storage is zero: enumeration does not materialize file bodies. RSS is sampled by
the separate server process, including while the native client holds Python's GIL.
Sequential measurements share a warmed allocator; absolute RSS and growth are
both shown. These are local protocol benchmarks, not appliance throughput claims.

The original bulk WebDAV listing exceeded the 64 MiB growth bound at 100,000
entries. Incremental XML parsing removes completed response elements and passes
the same bound. S3 uses native continuation; SFTP streams directory responses.
Connection pooling remains deferred.

Reproduce from `backend/` with:

```sh
./scripts/test.sh contract -q tests/contract/services/test_remote_discovery.py --junitxml=/tmp/discovery.xml
```

The suite also exercises stalled S3, WebDAV and SFTP requests under short scan
deadlines. Real Nextcloud, OpenSSH and S3 scan/download API coverage runs in the
remote Library end-to-end suite.
