# ADR-0003: Storage capability tiers, and OpenDAL as an additive adapter

- **Status**: Accepted and implemented
- **Date**: 2026-08-27
- **Deciders**: maintainers
- **Tracking issue**: [#90](https://github.com/xiao-villamor/PrintStash/issues/90)

## Context

`StorageBackend` (`backend/app/services/storage_backend.py`) is not a
portability shim. Roughly a quarter of its surface implements one safety
protocol: **create-only writes carrying positive proof of which exact object
was created**, so that a rollback can never destroy bytes it did not write.
`CreationReceipt` is that proof, and each adapter earns it natively:

- **Local** stages through `mkstemp`, `fsync`s, publishes with
  `os.link(..., follow_symlinks=False)` (an atomic no-replace publication),
  `fsync`s the directory, and fingerprints the result as
  `(st_dev, st_ino, st_ctime_ns, st_size)`.
- **S3** issues `PutObject` with `IfNoneMatch="*"`, stamps a per-operation
  token into object metadata, and records the returned `VersionId`.

Self-hosters keep asking for storage PrintStash does not speak — Nextcloud over
WebDAV, a NAS over SFTP, occasionally a consumer drive. Writing an adapter per
protocol is not tractable for a project this size.

[Apache OpenDAL](https://opendal.apache.org/) is a Rust data-access layer with
Python bindings that speaks ~60 storage services behind one interface. The
question this ADR settles is not "is OpenDAL good" — it is **which part of the
storage layer may be expressed through a uniform interface, and which part must
not be.**

The full image builds the exact Apache OpenDAL **0.58.2** Rust source. OpenDAL's
Python distribution uses an independent release number and that source builds
the exact `opendal==0.47.6` wheel; these are the same pinned source release, not
two different core versions.

### What we verified

`write_with_if_not_exists` is declared by exactly **fs, s3, gcs, azblob, azdls,
oss, cos, ghac**. Since v0.52 write APIs return `Metadata` (etag, version,
last-modified) rather than `()`, though per-service population is still being
filled in under RFC-5556. `WriteOptions` carries `if_not_exists`, `if_match`,
`if_none_match`, `user_metadata`, `chunk`, and `concurrent`.

Sorted against what PrintStash would actually gain:

| | Backends |
|---|---|
| Conditional create **and** user metadata | `fs`, `s3` *(both already implemented by hand)*, `gcs`, `azblob`, `azdls`, `oss`, `cos` |
| Neither | `webdav`, `sftp`, `ftp`, `onedrive`, `googledrive`, `dropbox`, `pcloud`, `koofr`, `yandexdisk`, … |

The backends that arrive with full semantics are the three hyperscaler object
stores. The backends people actually ask for are in the second row. Any plan
that supports only the first row buys almost nothing.

## Decision

### 1. Union, not intersection: OpenDAL never becomes the only path to storage

**OpenDAL's API is the intersection of ~60 backends.** It models
path-addressed objects because that is what all of them share. PrintStash's
local guarantee is *stronger* than the object-store guarantee, so routing local
through a uniform interface would silently downgrade it. We want the **union**,
where each adapter contributes its strongest available primitive, and the tier
system (decision 2) makes the difference legible instead of hiding it.

The concrete loss, if local were migrated:

> *POSIX has no unlink-if-inode-still-matches primitive. A check followed by
> unlink has a TOCTOU window that could remove a newly mounted or concurrently
> replaced path.* — `LocalStorageBackend._quarantine_owned`

`_quarantine_owned` exists because **a local file cannot be safely deleted by
path**. It `os.replace`s the file into a random same-directory quarantine name,
re-verifies the fingerprint on the *moved* inode, and only then unlinks — so the
only inode ever destroyed is one proven to be ours. OpenDAL's `fs` service
offers `delete(path)`: path-addressed, no inode identity. `rollback_create`
would become a bare unlink with an open race, and if a self-hoster's Syncthing,
rsync, or backup tool replaced that path in the meantime, we would delete their
file.

The same applies to `adopt_existing` (`O_NOFOLLOW` plus matching `fstat`
snapshots around the hash — an object API has no descriptor to hold open),
`_fsync_directory`, `_assert_no_managed_escape`, and
`verify_destructive_access` (an `mkstemp` probe per parent directory, because
nested ACLs and read-only submounts differ beneath one configured root).

This matters **more** for local than for remote, not less: a self-hoster's
`/data/files` is a shared, multi-writer directory. An S3 prefix usually is not.

**Local is never migrated to OpenDAL.** Not in this change, not later.

#### …but local's capabilities are probed, not assumed

The corollary bites an install we already ship. A self-hoster whose library
lives on a NAS or Nextcloud today mounts the share into the container — NFS,
SMB/CIFS, `sshfs`, `rclone mount`, `davfs2` — points `VAULT_DATA_DIR` at it, and
runs `LocalStorageBackend` on top. Every primitive above then rests on a
filesystem that may not provide it:

- **Hardlinks.** SMB/CIFS, `davfs2`, and `rclone mount` generally do not support
  `link(2)`, so the atomic no-replace publication is unavailable.
- **Directory `fsync`.** Frequently a no-op on FUSE and network filesystems, so
  the durability step silently does nothing.
- **Inode stability.** FUSE layers synthesize inode numbers and several reassign
  them per mount session. Receipts persist (`CaptureUploadSlot.receipt_json`),
  so after a restart a stored receipt can stop matching the object it describes
  — `creation_matches` then returns `False` for the remainder of that object's
  life, and every verification and rollback path fails closed permanently.

Nothing detects any of this today: `LocalStorageBackend.ensure_setup()` performs
two `mkdir`s, and `docs/known-limitations.md` carries no warning. PrintStash
reports the backend as `local` and behaves as though every guarantee holds.

Therefore `LocalStorageBackend.capabilities()` is **probed at
`ensure_setup()`**, not hardcoded: attempt a hardlink inside the data dir,
attempt an `O_DIRECTORY` fsync on it, and classify the filesystem beneath it.

The classifier already exists. `external_library.detect_fs_kind()` reads
`/proc/self/mountinfo` and returns `local` / `network` / `unknown`, written for
exactly the analogous reason — *"real-time watching only works on local
filesystems; on network mounts the kernel does not deliver inotify events"* — and
it already treats fuse and virtiofs as `unknown`. It moves to a shared module and
gains a second caller. **This ADR adds no new filesystem-detection machinery; it
notices that PrintStash already distrusts network mounts in one subsystem and
extends that distrust to the one where the stakes are bytes rather than
latency.** A mounted-share install then reports Guarded or Unguarded honestly
instead of claiming Verified.

This is the highest-value item in the ADR for existing installs, it needs no
OpenDAL, and it reframes what OpenDAL is for. Over a mount, PrintStash claims
Verified and cannot deliver it. Over an OpenDAL WebDAV adapter it claims
Unguarded and delivers exactly that, plus the ledger's conditional create
(decision 3). **Correctly labelled Unguarded is a stronger real position than
mislabelled Verified** — the labelling, not the protocol, is what this ADR
actually buys.

### 2. Capability axes, with tiers derived from them

Not a `AtomicStorageBackend` / `BestEffortStorageBackend` hierarchy. Two
reasons:

1. `StorageBackend`'s own contract forbids what a hierarchy invites — *"Callers
   must never branch on the concrete backend type."* Named subclasses are an
   engraved invitation to `isinstance` checks at call sites.
2. It is not one bit. A backend can hold any subset: `fs` has conditional
   create and a real path but no durable version identity; `webdav` has atomic
   rename but no conditional create; `s3` has every guarantee but no path.

```python
class ObjectIdentity(StrEnum):
    """How a receipt binds to the exact bytes it was issued for."""
    INODE = "inode"      # local: (st_dev, st_ino, st_ctime_ns, st_size)
    VERSION = "version"  # object store with versioning: version_id
    ETAG = "etag"        # entity tag only — detects change, cannot address it
    NONE = "none"


@dataclass(frozen=True)
class StorageCapabilities:
    """What one bound adapter can actually promise. Computed once, at setup."""

    conditional_create: bool      # create fails rather than clobbers
    object_identity: ObjectIdentity
    verified_delete: bool         # can delete *only* the proven object
    conditional_replace: bool     # replace only while proof still holds
    namespace_ownership: bool     # can prove a key sits inside our root
    direct_path: bool             # a real Path exists (performance, not safety)

    @property
    def tier(self) -> StorageTier:
        if not self.conditional_create:
            return StorageTier.UNGUARDED
        if self.verified_delete and self.conditional_replace:
            return StorageTier.VERIFIED
        return StorageTier.GUARDED
```

The three tiers, each named for what it **guarantees**, not for what it lacks:

| Tier | Guarantee | Backends |
|---|---|---|
| **Verified** | A create never clobbers; a rollback destroys only our own bytes | local, versioned S3, versioned GCS/azblob |
| **Guarded** | A create never clobbers; a failed operation leaks bytes we cannot positively reclaim | **unversioned S3 (shipping today)**, unversioned GCS/azblob |
| **Unguarded** | Best effort. Concurrent writes to one key can lose data | webdav, sftp, ftp, consumer drives |

**The middle tier already ships.** `S3StorageBackend.rollback_create` returns
`False` and logs *"S3 object has no immutable version identity"* whenever the
bucket is unversioned. This ADR does not introduce a taxonomy — it names one
PrintStash has had since S3 support landed, and the OpenDAL adapter then slots
into a structure that already has a home for it.

Consequence worth stating plainly: the tier machinery is testable and shippable
against unversioned MinIO **before any OpenDAL code exists**.

`object_identity` stays an enum rather than collapsing into a boolean because
`INODE` and `VERSION` both reach Verified by different mechanisms, and
`/health` and support threads need to say which.

### 3. A DB intent ledger, which supplies conditional-create where the backend cannot

PrintStash already built this once, for a single path. `CaptureUploadSlot`
carries `state` (`PENDING`), `storage_key` **UNIQUE**, `sha256`, `size_bytes`
and `receipt_json`. Generalise it:

```python
class StorageObject(SQLModel, table=True):
    """Intent ledger: every key PrintStash means to own, recorded before write.

    The unique constraint on `key` is the conditional-create primitive for
    backends that have none of their own.
    """
    __tablename__ = "storage_objects"

    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(max_length=2048, unique=True)
    state: StorageObjectState = Field(default=StorageObjectState.PENDING, index=True)
    backend: str = Field(max_length=32)
    namespace: str = Field(max_length=512)
    size_bytes: Optional[int] = None
    sha256: Optional[str] = Field(default=None, max_length=64, index=True)
    receipt_json: Optional[str] = Field(default=None, sa_column=Column(Text))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    committed_at: Optional[datetime] = Field(default=None, index=True)
```

Three things it buys:

**(a) Conditional create on any backend.** Insert the row *before* writing
bytes; the unique constraint serialises two concurrent writers. Instances share
a database, so this holds for multi-instance deployments too. It does not stop
an *external* writer (Nextcloud's own web UI dropping a file at that path) —
which is exactly the residual risk the Unguarded warning must name.

**(b) An orphan sweep that substitutes for rollback.** `PENDING` rows older
than a threshold are orphans; a sweeper reclaims them. This recovers leaked
bytes on **every** tier, including unversioned S3 today, with no OpenDAL
involved. It is a weaker delete than `rollback_create` — by key, not by proof —
so it is gated: only keys this ledger created and never committed, and
`size_bytes` (plus etag where available) re-verified immediately before
deleting.

**(c) Content proof as a last-resort `creation_matches`.** With `sha256` and
`size_bytes` recorded, identity can be proven by re-reading and hashing on a
backend with no metadata at all. Absurd for a 400 MB mesh; entirely reasonable
for thumbnails and cover images. Therefore a per-keyspace option, never a
global one.

**The one correctness rule.** The database and the store are two systems and
the write is not atomic across them. Therefore: **always insert-then-write,
never write-then-insert.** Under that ordering every failure mode is an orphan
(safe, sweepable) and none is a clobber (unsafe). That ordering *is* the
correctness argument and belongs in a comment at the seam.

### 4. Unverified backends are opt-in at boot, via environment only

`ensure_setup()` refuses to bind an Unguarded adapter unless
`VAULT_STORAGE_ALLOW_UNVERIFIED=true`.

```python
def ensure_setup(self) -> None:
    caps = self.capabilities()
    if caps.tier is StorageTier.UNGUARDED and not settings.storage_allow_unverified:
        raise RuntimeError(
            f"storage backend {self.backend_name!r} cannot create objects without "
            "replacement. Set VAULT_STORAGE_ALLOW_UNVERIFIED=true to accept the "
            "consequences listed at <docs link>."
        )
```

Environment-only, not a runtime-config overlay value, for three reasons:

1. **Circularity.** The flag gates `ensure_setup()`, which runs at boot. A value
   the web UI can toggle cannot gate the thing that must already have booted to
   serve that UI.
2. **Blast radius.** A safety acknowledgement flippable from a web session is
   one compromised session away from being flipped.
   `runtime_config.update_storage` may keep choosing among *permitted*
   backends; it must not choose what is permitted.
3. **Deliberateness is the point.** Editing a deploy file is the act we want a
   self-hoster to perform.

The UI's role is display and discovery: show the derived tier and its
consequences read-only in Settings and `/health`, and grey out unverified
backends in the storage form with *"set `VAULT_STORAGE_ALLOW_UNVERIFIED=true`
to enable"* — discoverable without being toggleable.

### 5. Warnings are derived per axis, not per tier

A tier label is too coarse to act on. The operator-facing text is assembled
from the axes that are false, so it names the failure the operator will
actually meet:

| Axis absent | What the operator is told |
|---|---|
| `conditional_create` | Two simultaneous uploads of the same revision can silently overwrite each other. |
| `object_identity = none` | PrintStash cannot verify a file is the one it wrote; failed uploads leave files behind that need manual cleanup. |
| `verified_delete` | Interrupted uploads leak files; the orphan sweep reclaims them. |
| `namespace_ownership` | PrintStash cannot confirm a file is inside its own folder before deleting it. |

Honesty about severity is part of the design, and PrintStash's own keyspace
makes the honest version narrower than "atomicity is unavailable":

- `blob_key(slug, version, filename)` — a lost update loses a revision. Real.
- `stl_cache_key(sha256)` — content-addressed; a collision writes identical
  bytes. Benign.
- `thumbnail_key(file_id)` — overwriting is the intended behaviour. Benign.

Because the table is keyed by axis, it is directly testable: one case per axis,
asserted against each adapter's declared capabilities.

### 6. Hard-delete stays gated above the flag

`verify_destructive_access` and `_owned_namespace` exist because a wrong answer
during hard delete is permanent. On an adapter with
`namespace_ownership = False`, `services/storage_deletion` operates on faith.
Purge therefore requires explicit confirmation on a non-Verified backend **even
after** `VAULT_STORAGE_ALLOW_UNVERIFIED` is set. The blanket acknowledgement
covers ingest; it does not cover irreversible deletion.

### 7. A provider catalogue is the product surface. OpenDAL is never named in it

The user-facing configuration must not contain the word "opendal", a scheme
string, or an options blob. Those are implementation detail. A self-hoster
choosing Nextcloud picks **Nextcloud** and fills in a Nextcloud form; whether
that is served by OpenDAL's `webdav` service, a future native client, or
something else is ours to change without touching a single `.env` file.

So PrintStash models **every** backend as a first-class typed provider,
including the two that exist today, and OpenDAL becomes one *transport* behind
that catalogue rather than a configuration surface of its own.

#### Layering

```
StorageProvider          product vocabulary — what the operator picks
      |                  (local, s3, nextcloud, webdav, sftp, azure_blob, gcs)
      |  a ProviderConfig subclass: typed fields + ClassVar metadata
      v
TransportSpec            how we actually talk to it
      |                  (NATIVE_LOCAL | NATIVE_S3 | OPENDAL(scheme, root, options))
      v
StorageBackend           the existing ABC — unchanged
```

The catalogue is the single source of truth for **six** consumers that currently
drift apart: environment variable names, the ADR-0002 runtime overlay, the
Settings form, the documentation table, the tier badge, and
`docs/storage-providers.md`. One declaration feeds all of them.

#### The provider model

```python
class StorageProvider(StrEnum):
    LOCAL = "local"
    S3 = "s3"
    NEXTCLOUD = "nextcloud"
    WEBDAV = "webdav"
    SFTP = "sftp"
    AZURE_BLOB = "azure_blob"
    GCS = "gcs"


class TransportKind(StrEnum):
    NATIVE_LOCAL = "native_local"
    NATIVE_S3 = "native_s3"
    OPENDAL = "opendal"


@dataclass(frozen=True)
class TransportSpec:
    """Resolved, internal-only. Never rendered, never accepted from an operator."""
    kind: TransportKind
    scheme: str = ""
    root: str = ""
    options: Mapping[str, str] = field(default_factory=dict)
    secret_options: Mapping[str, str] = field(default_factory=dict)


class ProviderConfig(BaseModel, ABC):
    """One provider's typed, user-facing settings."""

    @abstractmethod
    def transport(self) -> TransportSpec: ...
```

One provider, in full — and it is the clearest argument for the whole design:

```python
class NextcloudConfig(ProviderConfig):
    server_url: HttpUrl = Field(title="Nextcloud URL",
                                description="e.g. https://cloud.example.com")
    username: str = Field(title="Username")
    app_password: SecretStr = Field(
        title="App password",
        description="Settings → Security → Create new app password.")
    folder: str = Field(default="/PrintStash", title="Folder")

    def transport(self) -> TransportSpec:
        # Nextcloud's WebDAV endpoint layout is our problem, not the user's.
        return TransportSpec(
            kind=TransportKind.OPENDAL,
            scheme="webdav",
            root=self.folder,
            options={"endpoint":
                     f"{self.server_url}/remote.php/dav/files/{self.username}"},
            secret_options={"username": self.username,
                            "password": self.app_password.get_secret_value()},
        )
```

Under a raw-scheme configuration the operator would have to know that
`/remote.php/dav/files/{username}` is where Nextcloud hides WebDAV. Under the
catalogue they type the URL from their browser's address bar. That difference
is the entire justification: **a provider is a piece of product knowledge, and a
scheme is a piece of library trivia.**

`local` and `s3` are providers too, and their `transport()` returns
`NATIVE_LOCAL` / `NATIVE_S3`. They gain the same typed model, the same generated
form, and the same tier badge — so the catalogue is uniform rather than "the two
real ones plus the OpenDAL ones".

#### Why the transport seam exists

The seam is not ceremony: **the provider→transport mapping is many-to-one, and
the knowledge on each side is different in kind.**

```
Nextcloud ─┐
ownCloud  ─┤
Seafile   ─┼──> OPENDAL(scheme="webdav")
Synology  ─┤
WebDAV    ─┘

Synology/TrueNAS ─┬──> OPENDAL(scheme="sftp")
SFTP             ─┘

Cloudflare R2 ─┐
Backblaze B2  ─┤
Wasabi        ─┼──> NATIVE_S3        (endpoint/region presets only)
MinIO         ─┤
S3            ─┘
```

Without the seam, that fan-in has to live somewhere worse. Two candidates, both
rejected:

**Put the product knowledge in the adapter.** `OpenDALStorageBackend` grows a
branch per product — it would have to know that Nextcloud serves WebDAV under
`/remote.php/dav/files/{username}`. The adapter's job is to speak a scheme; the
moment it knows about products, every new provider edits the most
safety-critical new file in the change.

**Let each provider build its own backend** (`ProviderConfig.build_backend()`).
Then every WebDAV-family provider re-implements operator construction, root
validation, and the capability probe, and the five of them will drift. It also
makes provider tests impossible to keep in `unit/`: asserting on a returned
`TransportSpec` is pure logic, while asserting on a constructed backend needs a
socket or a filesystem, which by our own tier policy pushes those tests to
`integration/`.

With the seam, each side holds exactly what it should:

| | Knows | Does not know |
|---|---|---|
| `ProviderConfig` | that Nextcloud hides WebDAV under `/remote.php/dav/files/{user}` | that OpenDAL exists |
| `TransportSpec` | scheme, root, options | which product produced it |
| `OpenDALStorageBackend` | how to speak a scheme and probe capabilities | that Nextcloud exists |

Three further payoffs fall out of it:

1. **Root validation happens once**, on `TransportSpec`, rather than being
   re-asserted by every provider — and given what an empty root does to
   `services/storage_deletion` (see "Root is mandatory"), once is what we want.
2. **A provider can change transport without touching its configuration.** If a
   native Nextcloud client ever replaced WebDAV, only `NextcloudConfig.transport()`
   changes — env vars, the generated form, and stored `SystemConfig` rows are all
   untouched. That is the "ours to change without touching a single `.env` file"
   promise at the top of this decision, and the seam is the only reason it is
   true.
3. **`NATIVE_S3` providers cost nothing.** R2, B2, Wasabi, and MinIO become
   first-class named providers that preset an endpoint and region over the S3
   adapter we already have — the Nextcloud trick applied to a backend we already
   ship, with no new transport, no new adapter, and no OpenDAL. This lands in the
   catalogue phase, before any OpenDAL code exists.

The cost is honest and small: for `local` and `s3` the `TransportSpec` carries
little beyond its `kind`. A uniform dataclass with two thin cases is better than
a catalogue with two special cases in it.

Dispatch stays a single readable function:

```python
def create_backend(provider: StorageProvider, config: ProviderConfig) -> StorageBackend:
    spec = config.transport()
    match spec.kind:
        case TransportKind.NATIVE_LOCAL:
            return LocalStorageBackend()
        case TransportKind.NATIVE_S3:
            return S3StorageBackend(spec)
        case TransportKind.OPENDAL:
            return OpenDALStorageBackend(spec)
```

#### The catalogue we intend to ship

Grouped by transport, because that is what determines cost and tier. "Native
knowledge" is what the provider encodes so the operator does not have to.

| Provider | Transport | Native knowledge it encodes | Expected tier |
|---|---|---|---|
| Local filesystem | `NATIVE_LOCAL` | — | Verified *(probed; a mounted share may be lower)* |
| S3-compatible | `NATIVE_S3` | — | Verified / Guarded by bucket versioning |
| Cloudflare R2 | `NATIVE_S3` | endpoint `https://{account_id}.r2.cloudflarestorage.com`, `region=auto` | as S3 |
| Backblaze B2 | `NATIVE_S3` | S3 endpoint per region | as S3 |
| Wasabi | `NATIVE_S3` | regional endpoint | as S3 |
| MinIO / Garage / SeaweedFS | `NATIVE_S3` | path-style addressing, self-hosted defaults | as S3 |
| **Nextcloud** | `webdav` | `/remote.php/dav/files/{username}`, app-password guidance | Unguarded |
| ownCloud | `webdav` | `/remote.php/dav/files/{username}` | Unguarded |
| Seafile | `webdav` | `/seafdav` | Unguarded |
| Synology DSM (WebDAV) | `webdav` | WebDAV Server package, ports 5005/5006 | Unguarded |
| Hetzner Storage Box | `webdav` / `sftp` | documented host and path layout | Unguarded |
| Generic WebDAV | `webdav` | — (typed escape valve *within* the family) | Unguarded |
| NAS over SFTP | `sftp` | host/port/user/key-or-password/path | Unguarded |
| Azure Blob Storage | `azblob` | container + account naming | Guarded |
| Google Cloud Storage | `gcs` | bucket + service-account JSON | Guarded |

The `NATIVE_S3` rows are the cheapest value in this ADR: four named products,
zero new code paths, and they remove the single most common S3 setup mistake —
a hand-typed endpoint.

Deliberately **out of the first cut**: Dropbox, Google Drive, and OneDrive. Not
because the transport is missing, but because each needs an OAuth authorization
flow with token storage and refresh, which is a larger change than this whole
ADR and unrelated to storage semantics. They stay candidates once a general
OAuth credential story exists.

Two open design questions the catalogue phase must settle:

- **SFTP key material.** A path to a key file inside the container, or an inline
  key in an `EncryptedText` column? A file path is simpler and matches how
  operators already mount secrets; an inline key is configurable from the UI.
- **Vendor rows that encode nothing.** Synology-over-SFTP is generic SFTP with a
  different label. A vendor entry earns its place only when it removes real
  knowledge from the operator, as Nextcloud does; otherwise it is a docs
  paragraph pointing at the generic provider, not a catalogue row.

#### The registry is a discriminated union, not a dict

A `Mapping[StorageProvider, ProviderSpec]` looks natural and is the wrong shape:
its value type has to be erased to `ProviderSpec[Any]` for the map to be
heterogeneous, nothing ties a spec to the config class it validates, and the type
checker cannot tell you when a provider is unhandled.

So the catalogue **is** a discriminated union, and each provider carries its own
metadata as `ClassVar`s:

```python
class ProviderConfig(BaseModel, ABC):
    """One provider's typed, user-facing settings."""

    provider: ClassVar[StorageProvider]
    label: ClassVar[str]
    blurb: ClassVar[str]
    category: ClassVar[ProviderCategory]
    expected_tier: ClassVar[StorageTier]
    docs_anchor: ClassVar[str]

    @abstractmethod
    def transport(self) -> TransportSpec: ...


class NextcloudConfig(ProviderConfig):
    provider = StorageProvider.NEXTCLOUD
    label = "Nextcloud"
    category = ProviderCategory.WEBDAV
    expected_tier = StorageTier.UNGUARDED
    ...

    kind: Literal[StorageProvider.NEXTCLOUD] = StorageProvider.NEXTCLOUD
    server_url: HttpUrl
    app_password: SecretStr
    ...


AnyProviderConfig = Annotated[
    LocalConfig | S3Config | R2Config | NextcloudConfig | WebdavConfig | SftpConfig
    | AzureBlobConfig | GcsConfig,
    Field(discriminator="kind"),
]
PROVIDER_CONFIG = TypeAdapter(AnyProviderConfig)
```

Three things this buys that the dict cannot:

1. **Parsing returns a concrete type.**
   `PROVIDER_CONFIG.validate_python(raw)` is statically a union member, not a
   `ProviderConfig` base or a `dict`. Callers narrow with `match` and never cast.
2. **Exhaustiveness is checked.** `match config: case NextcloudConfig(): …` makes
   pyright flag an unhandled provider at check time. A dict keyed by enum can
   only fail at runtime with a `KeyError`.
3. **Metadata cannot drift from its config**, because it lives on the same class.
   The `repo/` test that would have asserted "every enum member has a spec"
   becomes structurally unnecessary.

`ProviderCategory` is the picker's grouping from decision 10, declared once here
rather than in the frontend.

#### Where generics do and do not earn their place

Generics are worth it exactly where a function must not be handed a mismatched
pair:

```python
C = TypeVar("C", bound=ProviderConfig)

def bind(config: C, backend: StorageBackend) -> BoundStorage[C]: ...
```

They are **not** worth it on `StorageBackend`, which never sees a config after
construction, nor on `TransportSpec`, which is a concrete product type that
deliberately forgets which provider produced it (decision 7's whole point). A
generic `TransportSpec[C]` would re-introduce the coupling the seam exists to
remove.

#### `dict[str, Any]` appears in exactly one place

Persistence needs a scalar column because SQL has no sum types — but **nothing
above the persistence edge sees an untyped mapping.** The conversion is confined
to a `TypeDecorator`, which is the pattern this codebase already uses for
`EncryptedText`:

```python
class ProviderConfigJSON(TypeDecorator[ProviderConfig]):
    """Typed at the boundary: the union is recovered on load, once."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value.model_dump_json(exclude=_secret_fields(type(value)))

    def process_result_value(self, value, dialect):
        return PROVIDER_CONFIG.validate_json(value)
```

`SystemConfig.storage_provider_config` is typed `ProviderConfigJSON`, so
attribute access yields `AnyProviderConfig`, not a dict. Service code, the API
layer, and `create_backend` all work in typed values; the JSON text exists only
between `process_bind_param` and the disk.

#### Secret-ness is expressed by the type

`_secret_fields()` above is derived by inspecting which annotations are
`SecretStr` — no parallel flag list to forget:

```python
def _secret_fields(model: type[ProviderConfig]) -> set[str]:
    return {name for name, f in model.model_fields.items()
            if f.annotation is SecretStr}
```

One declaration then drives three behaviours: the field is stored in the
`EncryptedText` column rather than the plaintext one, the API descriptor reports
`secret: true`, and the form renders it masked and write-only. Two `repo/`
invariants keep that honest:

- every `StorageProvider` member appears in `AnyProviderConfig` (the one thing
  the union cannot enforce structurally);
- no field whose name matches `/password|secret|token|credential|key/` is
  annotated as anything other than `SecretStr` — a lint against a credential
  silently landing in the plaintext column.

#### Configuration surface

```
VAULT_STORAGE_PROVIDER=nextcloud
VAULT_NEXTCLOUD_URL=https://cloud.example.com
VAULT_NEXTCLOUD_USERNAME=printstash
VAULT_NEXTCLOUD_APP_PASSWORD=…
VAULT_NEXTCLOUD_FOLDER=/PrintStash
```

Env var names are derived from the provider's field names under a
`VAULT_{PROVIDER}_` prefix, so adding a provider adds its variables without a
second declaration. **No `VAULT_OPENDAL_*` variable exists**, and neither does a
scheme or an options blob.

Persistence follows the `SystemConfig` pattern already used for OIDC: a
`storage_provider` column, a `storage_provider_config` column typed
`ProviderConfigJSON` (so attribute access yields the discriminated union, never a
dict), and a `storage_provider_secrets` column typed `EncryptedText` holding only
the `SecretStr` fields. The existing `data_dir` / `s3_*` columns stay
exactly as they are, so no self-hoster's stored configuration is rewritten.

The Settings form is **generated** from the selected provider's model — field
titles, descriptions, and secret-ness all come from the pydantic `Field`s. There
is no per-provider React component, and adding a provider does not require a
frontend change.

#### Why a DSN is rejected as well

The same argument as against exposing schemes, plus:

1. **Storage config is already overridden field-by-field.** `SystemConfig`
   stores discrete columns and `runtime_config` layers them into the ADR-0002
   overlay. One opaque string cannot be rendered as a form, and partial updates
   become string surgery.
2. **Secrets.** A DSN embeds the credential in the value that gets logged,
   echoed by `/health`, and written to the database. Typed fields separate
   secret from non-secret structurally, so both can be echoed with no redaction
   pass — `create_backend` already logs the bucket name today, which under a DSN
   would be a credential leak.
3. **Upstream URI mapping is not a stability contract we control.**
   Self-hosters' `.env` files *are* our compatibility contract.
4. **Boot-time validation.** A typed model rejects a malformed URL at import;
   an opaque bag fails at first write.

#### No generic escape hatch

Deliberately **no** passthrough for arbitrary OpenDAL schemes. Adding a provider
is a small, mechanical PR — a pydantic model, a `transport()`, a registry entry —
and that PR is forced to add a `contract/` test for its scheme. A passthrough
would let an operator reach an untested scheme whose tier we could not state
truthfully, which defeats the one thing this ADR is for. The cost of "support one
more" is low precisely so that the escape hatch is unnecessary.

#### Root is mandatory

`TransportSpec.root` is validated non-empty for every OpenDAL provider, because
`walk_keys("")` and `usage("")` drive `services/storage_deletion`. With an empty
root on a shared container, a purge would enumerate and delete data that is not
ours. Carry `f"{scheme}/{root}"` into `CreationReceipt.namespace`, exactly as the
S3 adapter carries `f"{bucket}/{prefix}"`.

#### Compatibility

`VAULT_STORAGE_BACKEND=local|s3` keeps working as a deprecated alias for
`VAULT_STORAGE_PROVIDER`, and `SystemConfig.storage_backend` keeps being read.
Self-hosters auto-upgrade; nothing in an existing `.env` may stop working.

#### SFTP authentication implementation note

OpenDAL core 0.58.2 exposes mounted-key SFTP but does not expose password or
encrypted-key authentication. The full image therefore keeps mounted-key SFTP
on OpenDAL and uses an internal operator-compatible AsyncSSH transport only for
password and key-passphrase modes. Both paths implement the same synchronous
`StorageBackend`, remain Unguarded, publish through temporary-key rename, and
run the same loopback contracts. This narrow fallback is not a generic second
storage abstraction and does not make additional providers selectable.

### 8. Retire the two bucket-administration writes

Independent of OpenDAL, and worth doing on its own merits:

- **`_apply_lifecycle_policy` — remove.** It writes an `Expiration` rule onto
  the user's bucket: the application configuring automatic deletion of the
  user's data. It directly contradicts `destructive_lifecycle_findings`, whose
  entire purpose is to *warn* about such rules — one method installs the hazard
  the other reports. It also forces `s3:PutLifecycleConfiguration` into the
  credential we ask self-hosters to create. Document the recommended rule
  instead; let operators apply it in their provider's console.
- **`_ensure_bucket` — remove the create half.** Auto-create requires
  `s3:CreateBucket`, over-privileging the credential, and *succeeds* against a
  mistyped endpoint or wrong region by creating a bucket the operator never
  intended. Replace with a probe that fails loudly.
- **`destructive_lifecycle_findings` — keep.** Read-only, needs only
  `s3:GetLifecycleConfiguration`, already degrades to `[]` on any error, and it
  is the one that protects users.

This shrinks the storage layer's boto3 surface to a single optional read-only
call. It does **not** remove the dependency: `app/services/backup.py` builds its
own boto3 client (`_get_backup_s3`) against a different bucket with different
credentials (`backup_s3_*`), doing `put_object`, `head_object`, `delete_object`
and `list_objects` for backup archives. boto3 stays for backup regardless of
anything decided about storage, and the two are correctly decoupled — different
credentials, different blast radius.

### 9. S3 stays native on boto3. This is a decision, not a deferral

OpenDAL's S3 service looks like it could express the whole data plane —
`if_not_exists`, `if_match`, `user_metadata`, `chunk`/`concurrent` for the
multipart threshold, versioned delete, presigned reads. Measured against the
published Python binding, it cannot express the part that matters.

**`Operator.write()` returns `None` in `opendal` 0.47.6.** The Rust core returns
`Result<Metadata>`, but that has not reached the Python binding, so a create
yields no etag and no `version_id`. `stat()` does expose `.version`, but a
follow-up stat is racy by construction: it reports whichever version is current,
which may be someone else's write. That is precisely the substitution
`CreationReceipt` exists to make impossible, and without it
`S3StorageBackend.rollback_create` has nothing to verify against.

**An OpenDAL-backed S3 adapter therefore lands in Guarded, not Verified.**
Migrating would demote a backend that is Verified today. Measured against what
the migration would buy:

| | boto3 (today) | OpenDAL |
|---|---|---|
| Tier for a versioned bucket | **Verified** | Guarded — no write-side version identity |
| Installed size | 22.7 MB | 39.4 MB (single 38.6 MB `.so`, not trimmable) |
| `import` + client construction | ~0.3–0.6 s | ~0.01 s |
| Bulk transfer | network-bound | network-bound |

Faster startup and one fewer code path do not buy a tier demotion on the most
safety-critical code in the repository. **S3 remains a hand-written boto3
adapter.** This is not "later" — revisiting it requires a specific upstream
change (the Python binding returning write metadata including `version_id`),
and until that lands there is nothing to weigh.

The local backend is excluded for the reasons in decision 1, which no upstream
release can change. So OpenDAL's scope is exactly the backends PrintStash has no
adapter for, and that scope is not expected to grow.

### 10. The provider picker is category-first, and the form is server-described

A flat selector is already ~15 rows and grows with every provider. It is also
the wrong shape: it makes the operator scan an undifferentiated list, then
discover the safety consequence only after committing to a form.

So the picker is **two-step, grouped by transport family** — which is the same
axis that determines tier, so navigating the picker teaches the guarantee story
instead of springing a badge at the end:

```
Step 1 — category (5 cards, no scrolling)
  ┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
  │ This machine │ S3-compatible│  Nextcloud   │   NAS over   │    Cloud     │
  │              │object storage│  & WebDAV    │     SFTP     │  providers   │
  │  ✓ Verified  │  ✓ Verified  │ ⚠ Unguarded  │ ⚠ Unguarded  │  ~ Guarded   │
  └──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘

Step 2 — provider within the family (2–6 rows, each with its tier badge)
Step 3 — the generated form for that provider
```

The tier badge appears at **step 2, before** the operator fills anything in.
"Generic WebDAV" sits last inside its own family as the typed escape valve.

This composes existing `components/ui/` primitives only — `card`, `tabs`,
`badge`, `input`, `modal` — so it needs **no new primitive**, which matters given
`DESIGN.md`'s compose-don't-hand-roll rule and its zero-counts. A searchable
combobox would have to be built from scratch; a category grid does not.

The form is **described by the server**, not coded per provider:

```
GET /api/v1/storage/providers
[
  { "id": "nextcloud", "label": "Nextcloud", "category": "webdav",
    "blurb": "Store the library in a Nextcloud folder.",
    "expected_tier": "unguarded",
    "consequences": ["Two simultaneous uploads of the same revision …"],
    "docs_url": "…/storage-providers.md#nextcloud",
    "fields": [
      { "name": "server_url", "label": "Nextcloud URL", "type": "url",
        "help": "e.g. https://cloud.example.com", "secret": false,
        "required": true },
      { "name": "app_password", "label": "App password", "type": "string",
        "help": "Settings → Security → Create new app password.",
        "secret": true, "required": true }
    ] }
]
```

Field metadata is projected straight from the provider's pydantic model —
`title`, `description`, `SecretStr`-ness, required-ness. **Adding a provider is a
backend-only change**: no frontend edit, no new route, no new translation key
beyond the strings the model already carries. `secret: true` fields render masked
and are write-only; the API never returns a stored secret's value, only whether
one is set.

### 11. Documentation is generated from the catalogue, and "provider" gets disambiguated

**A naming collision exists today.** `docs/provider-support.md` is titled
*"Printer Provider Support"*. Introducing storage providers overloads a word that
`CONTEXT.md` already flags as ambiguous for "Backend". Therefore `CONTEXT.md`
gains binding entries — **Storage provider**, **Transport**, and the three tier
names — and the unqualified word "provider" continues to mean the printer kind,
matching existing usage. Storage code says "storage provider" in full.

Documentation lands in five places, each with a distinct audience:

| Where | Audience | Content |
|---|---|---|
| **`docs/storage-providers.md`** *(new)* | operators deploying | one section per storage provider: env vars, a worked example, its tier and what that implies, setup gotchas |
| `CONTEXT.md` | contributors and agents | binding vocabulary: Storage provider, Transport, Verified/Guarded/Unguarded |
| `docs/known-limitations.md` | operators diagnosing | the network-mount warning **currently absent**, plus per-tier caveats |
| `UPGRADE.md` | upgraders | `VAULT_STORAGE_BACKEND` → `VAULT_STORAGE_PROVIDER` alias; the dropped `s3:CreateBucket` / `s3:PutLifecycleConfiguration` grants |
| `.env.example` | first-time setup | the new variables, commented |

The per-provider table in `docs/storage-providers.md` is **generated from
`AnyProviderConfig`**, with a `repo/` test asserting the document matches the
union —
the same anti-drift pattern as `test_openapi_contract.py` and
`test_ci_workflows.py`. That makes documentation the **sixth** consumer of the one
declaration, and the only one a human could otherwise forget to update.

Each provider section states its tier and consequences in the operator's terms,
generated from the same per-axis table as the boot log and the UI. One source, so
the docs cannot promise what the probe denies.

### 12. External libraries keep the local-filesystem *transport*; the catalogue reaches them later

Two questions hide inside "should external libraries get provider support", and
they have different answers.

**Should they read bytes through a remote transport instead of a mount?** Not on
the strength of anything in this ADR — the argument that justifies decisions 1–11
does not transfer, and one that runs the other way does (below).

**Should they be *configured* through the same provider catalogue?** Yes,
eventually, and for a reason none of the below touches: **coherence.** Once the
vault can be configured as "Nextcloud, this URL, this app password", an external
library still demanding `/mnt/nextcloud/PrintStash` leaves one product with two
ways to name a remote storage location. That is a real cost, it is a
configuration cost rather than a correctness one, and it is tracked separately in
the external-library issue rather than settled here.

What follows is the case against the *transport* migration, which stands on its
own and is what this decision fixes.

#### The safety argument does not transfer

Everything hazardous about running the *vault* on a mounted share is write-side:
`os.link` publication, directory `fsync`, inode fingerprints, receipt
verification, quarantined rollback. External libraries perform **none** of those
on source bytes. Per `CONTEXT.md` the folder is the source of truth, PrintStash
never overwrites or deletes a linked file's bytes, and trash/GC skip external
blobs. `get_backend()` appears in `external_library.py` exactly once, for a
derived thumbnail that lands in vault storage — never on the external root.

So for a read-only external library, **a mount is not a dangerous workaround; it
is simply how you read a remote folder.** There is no guarantee being silently
downgraded, because there is no guarantee in play.

#### A mount is strictly better here, not merely adequate

Real-time watching works over a mount and can never work over a transport.
`watchfiles` gets inotify events on a local filesystem; WebDAV and SFTP have no
change-notification mechanism at all. Moving external libraries onto providers
would **remove a capability** that mounting provides.

#### There is no performance case either

Indexing a remote library downloads every file once regardless of transport — the
hash and the thumbnail both need full bytes, and `_reindex_changed` re-hashes on
any size/mtime change. FUSE and OpenDAL push the same bytes over the same wire.
The migration would buy zero throughput.

#### External libraries are the model to copy, not the thing to fix

This subsystem already does what decision 1 asks the vault to start doing:

- `detect_fs_kind()` classifies the filesystem from `/proc/self/mountinfo`.
- `ExternalLibrary.fs_kind` **persists** that classification.
- The API **returns** it, alongside a computed `watch_active`.
- `should_watch()` **degrades a feature honestly** on the strength of it —
  `AUTO` declines to watch anything that is not `local`.

Detect, persist, expose, degrade. The vault does none of the four. **The
borrowing direction is external-library → vault**, which is precisely what
decision 1's probe does by promoting `detect_fs_kind` to a shared module.

#### What the follow-up is actually for

Given the above, a provider-backed external library is justified by setup
ergonomics and product coherence, **not** by safety or throughput — and it must
be scoped and reviewed on those terms. It also has to confront the one place
where mounting is genuinely superior: real-time watching works over a mount and
can never work over a transport, so such a library is scheduled-scan-only by
construction and needs a resting state distinct from "network mount, might work
someday".

A second, sharper trigger exists for the transport itself: an operator who
**cannot** mount — a managed container platform forbidding FUSE or privileged
containers, with files in a hosted Nextcloud. Narrow but real.

Either way the work is pre-scoped:

1. `File.path` becomes a storage key plus a provider reference, changing what
   `path` and `is_external` mean — a migration, not a seam reuse.
2. Write-back (`ingestion.resolve_write_target`) genuinely writes to the external
   root and is collision-safe today only because the local backend never
   overwrites. It needs the same tier gate as vault writes.
3. Watch mode becomes permanently unavailable for such a library, so
   `AUTO`/`EVENTS` need a third resting state distinct from "network mount".

The catalogue is therefore defined as **storage-role-agnostic**: nothing in
decisions 1–11 assumes the vault is its only consumer, and adding a second one
requires no decision here to be reopened. A first cut should be **read-only,
write-back excluded**, which removes the write-side tier gate entirely and covers
the realistic case — a folder the user curates with their own tools.

## Consequences

**Gained**

- WebDAV/Nextcloud, SFTP-to-NAS, Azure, GCS, and the consumer drives become
  reachable, each labelled with what it does and does not guarantee.
- Unversioned S3 stops being an unnamed middle case and gets honest reporting.
- The orphan sweep reclaims leaked bytes on backends we already support.
- One place decides tier and warnings, so `/health`, Settings, and the boot log
  cannot drift apart.
- **A uniform provider catalogue for every backend, including the two we already
  have.** Env var names, the runtime overlay, the Settings form, the docs table,
  the tier badge, and `docs/storage-providers.md` are all generated from one
  declaration, so adding a storage provider is a backend-only change and the six
  surfaces cannot disagree — with a `repo/` test enforcing it.
- **`CONTEXT.md` gains the vocabulary** (Storage provider, Transport, and the
  three tier names), which the cloud seam will need whether or not OpenDAL ever
  lands.

**Deliberately not gained**

- Remote external libraries — deliberately, not for lack of time. The write-side
  argument does not apply to a folder we never write to, a mount is the only way
  real-time watching can work at all, and the byte cost of indexing is identical
  either way. Decision 12 records the one trigger that would reopen it.

**Accepted costs**

- A Rust native extension enters the wheel set. Mitigated by `opendal` being an
  optional extra: a self-hoster on local or S3 never installs it.
- Three adapters to maintain instead of two.
- The Unguarded tier means PrintStash ships a configuration in which concurrent
  writes can lose data. That is the deliberate trade, bounded by the ledger, the
  boot gate, and the delete gate.

**Risks**

- **A declared capability can be wrong, and has been silently wrong.** Measured
  on the published wheels: in `opendal` **0.45.1**, `Operator.write()` took
  untyped `**kwargs` and **silently swallowed `if_not_exists=True`** — a second
  write to the same key overwrote the first with no error, and `Capability`
  exposed no `write_with_if_not_exists` attribute to gate on. In **0.47.6** the
  option is enforced (`ConditionNotMatch`), unknown kwargs raise `TypeError`, and
  the capability flags are present. Separately, `write_with_if_not_exists` has
  been reported as declared-but-unenforced against azblob and s3/MinIO. So there
  exists a released window in which the primitive this whole design rests on was
  a no-op that no capability probe could detect. Therefore: the adapter **pins an
  exact `opendal` version**, and the `contract/` tier must prove conditional
  create rejects a second write against a real server per supported scheme. That
  coverage is the only thing standing between us and a silent clobber; it is not
  optional and not replaceable by reading the capability flags.
- **Write-returns-metadata has not reached the Python binding at all.**
  `write()` returns `None` as of 0.47.6, so *every* OpenDAL-backed scheme is
  capped at Guarded today regardless of what the underlying service supports.
  This is degradation by design rather than breakage, but it means a scheme's
  tier can improve across upstream releases and must therefore be **reported at
  runtime, never documented as a constant** — and it is the direct cause of
  decision 9.
- **Ledger drift.** A ledger that disagrees with the store causes a sweep to
  target a live object. Mitigated by insert-then-write ordering, by
  re-verification immediately before any sweep delete, and by the sweep being
  restricted to never-committed rows.

## Alternatives considered

**Replace both adapters with OpenDAL.** Rejected: decision 1. It downgrades the
default backend's guarantees to the intersection of sixty services.

**Support only backends with full semantics.** Rejected: it yields Azure, GCS,
and azdls — none of which the people asking are asking for — while excluding
every backend they are. The support question is the whole value.

**Warn at runtime instead of gating at boot.** Rejected: a warning after the
fact is read by nobody, and the failure it warns about is silent.

**Model tiers as an `AtomicStorageBackend` / `BestEffort` class hierarchy.**
Rejected: decision 2. It invites the `isinstance` branching the base class
explicitly forbids, and two classes cannot express six independent axes.

**Forward an OpenDAL URI, or expose scheme + options, from configuration.**
Rejected: decision 7. Both leak a library's vocabulary into the product's
configuration, which then cannot change without breaking `.env` files, and both
put knowledge on the operator (that Nextcloud serves WebDAV at
`/remote.php/dav/files/{username}`) that belongs in our code.

**A generic passthrough provider for unsupported schemes.** Rejected: it would
admit backends with no `contract/` coverage, whose tier we could not state
truthfully. Since a tier claim is the deliverable, an untestable backend is worse
than an absent one. Adding a provider is cheap enough that the hatch is
unnecessary.

## Testing

The implementation is covered by the executable
[63-row coverage matrix](0003-storage-capability-tiers-coverage.md). It has no
missing or skipped behaviours. The enduring test obligations are:

- `unit/` — tier derivation: one case per axis combination, plus one per
  warning-table row. Provider registry: every `StorageProvider` has a spec, every
  spec's model round-trips through the config/secrets columns, and every
  `transport()` produces a non-empty root.
- `repo/` — every `StorageProvider` member appears in `AnyProviderConfig`; no
  field named like a credential is annotated as anything but `SecretStr`; and
  `docs/storage-providers.md` matches the union, so a new storage provider cannot
  ship without its docs section and expected tier.
- `unit/` — a round trip through `ProviderConfigJSON` returns the same concrete
  subclass, and secret fields are absent from the plaintext column's payload.
- `integration/` — `GET /api/v1/storage/providers` returns a field descriptor per
  provider, never returns a secret's value, and rejects a write to an unverified
  provider without the flag.
- `frontend/src/**/__tests__/` — the picker renders categories from the API
  response and shows a tier badge before the form, with **no** provider names
  hard-coded in the component.
- `integration/` — the ledger's insert-then-write ordering, the orphan sweep's
  refusal to touch committed rows, and the boot gate's refusal without the flag.
- `contract/` — each supported OpenDAL scheme against a real server over a
  loopback socket, proving conditional create actually rejects a second write.
  This is the mitigation for the declared-but-unenforced risk; it is not
  optional.
- `e2e/` — one upload through an OpenDAL-backed vault.
