# Staged mesh outputs

Metadata and thumbnails publish independently before optional fingerprint extraction. Each callback commits its output under the current Job, generation, live Model/Artifact, source snapshot, recipe and attempt token; a notice follows that commit. A later timeout, resource refusal, process failure or cancellation cannot retract an already committed basic output. Thumbnail storage publication failures abort further native work.

## Native contract

The mesh worker writes MSH2 frames in requested order: geometry, thumbnail, then a terminal fingerprint/telemetry frame. One order is different: a 3MF that carries its slicer's own picture publishes that thumbnail first, before the mesh is read, because it needs nothing from the mesh; its geometry follows. Only a document-supplied thumbnail may precede geometry, and a rendered one that does is refused. A request may omit any basic output or request only a fingerprint. Typed geometry outcomes, canonical physical volume, source-scan coverage and geometry representation accompany each applicable output. Frames have contiguous sequence numbers and bounded payloads; duplicate, omitted, unsolicited, malformed and trailing data fail closed. Control payloads are capped at 64 KiB and the entire response at 32 MiB. Fingerprint records and source-coverage claims retain their independent validation.

The supervisor delivers validated basic frames while the child is alive. Terminal evidence becomes usable only after complete EOF and exit zero. Deadline, RSS, cancellation and tree cleanup stay active after stdout closes. A dedicated native monitor keeps deadline and process-tree RSS checks active while a synchronous publication callback blocks. It owns no application Session, Job or cancellation probe. The callback itself is not preempted: cancellation remains cooperative through publication fences and the main-thread checkpoint. Monitor withdrawal precedes process cleanup. A callback failure propagates its original exception after the native tree is reaped. Embedding and verification retain their existing EMB1/VRF1 formats and share the canonical value codec; their replies remain terminal results.

## Geometry lifetime

A 3MF scene that needs whole-scene topology is composed once. When optional fingerprint analysis is admitted, readonly float64 vertices and int64 faces survive the preview stage; the original Trimesh object and its topology caches are released before rendering. Analysis wraps the same buffers without recomposing placements, and releases them on completion or failure. The placed arrays remain resident during preview, so this trades some overlapping memory for avoiding a second materialization. Fingerprint-disabled or refused work retains no analysis buffers.

Oversized STL metadata and preview share the validated scan only inside the owning native process. Source identity and current preview budgets are checked again before using the scan; the second full source pass still validates every facet and EOF. The combined path requires two source passes. Scan hints never cross the subprocess protocol.

## Durable optional work

A mesh fingerprint continuation records source identity, algorithm, triangle cap, metadata recipe, regeneration snapshot and current execution authority in the same transaction that publishes metadata. It has its own token; metadata's READY token is retired independently. The derivative Work Source discovers due continuations even when metadata and thumbnail are already READY. Its existing bounded scan and producer controls still apply, and the next-due query includes future continuation availability.

Recovery validates the current context, retires obsolete input or retokens valid work for the current execution. An old result cannot mutate the new intent. Cache publication, follow-up system-run intent and exact-token retirement share one transaction. Existing READY fingerprint evidence survives late failure. A conflicting cache lease defers work until its expiry.

Native failures defer pending analysis with the existing capped exponential derivative backoff. Persisted attempt counts bound recovery across fresh Jobs: the last failed attempt publishes its closed failure cause and retires intent; recovery after a lost final attempt publishes WORKER_FAILED without launching another native process. Cancelled current execution intent is withdrawn by matching Job id, epoch, attempt, kind and subject; a replacement's token remains protected. The historical Job id is an execution snapshot without a retention-blocking foreign key. The Artifact foreign key cascades on purge.

## Upgrade and validation

The additive migration creates only the pending-input table, its due index and required constraints. Existing Artifact paths, thumbnails and volume evidence remain untouched. Upgrade/downgrade, offline DDL and model/chain parity are checked with real existing rows. No derivative recipe changes: staged timing does not change producer output or the fingerprint algorithm.

The validation matrix below names the independent assertion for each contract. Timings are observations from targeted checks, not a latency SLA.

## Runtime and numerical compatibility

The application and shared core require Python 3.14 or newer. Development, containers and CI select the current stable standard Python 3.14.8 build; retired Python versions have no application test lanes. The Python container stages use the Trixie image so the converter and application share their native ABI and the converter receives a supported Boost. The shared lock resolves NumPy 2.5.3, SciPy 1.18.1, Trimesh 5.1.1, Pillow 12.3.0 and lxml 6.1.3. Historical benchmark records and immutable fixtures retain the versions that produced them.

The update was checked against frozen render/view fixtures, signed volume and hull edge cases, exact neighbor ordering, and the versioned geometry evaluation corpus. An additional same-source comparison preserved geometry, physical volume, PNG bytes, view hashes and numerical fingerprint payloads. The serialized descriptor changes only its NumPy version attribution. That comparison is evidence for those fixtures; it does not claim bit identity across every BLAS implementation or machine. The output recipe and fingerprint algorithm remain unchanged.
