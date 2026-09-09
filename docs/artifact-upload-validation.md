# Resumable Artifact upload validation

Issue #103 is complete only when every applicable row below has an automated
assertion and no unexplained `❌` remains. The table records observable
behaviour, not implementation details; status changes as each slice lands.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---:|---|---|---|---|---|:---:|
| 1 | model upload characterization | existing ingestion | model file upload | staged bytes are hashed, leased, and persisted through the canonical Artifact path | integration | ❌ |
| 2 | G-code upload characterization | existing ingestion | G-code upload | metadata and Artifact persistence retain the original SHA-256 | integration | ❌ |
| 3 | revision upload characterization | existing ingestion | new revision for a Model | revision uses the same lease and persistence boundary | integration | ❌ |
| 4 | archive upload characterization | existing ingestion | supported archive | extracted Artifacts use the canonical persistence boundary | integration | ❌ |
| 5 | browser capture characterization | existing ingestion | browser capture file | declared size/hash are checked and a durable lease owns staging | integration | ❌ |
| 6 | URL import characterization | existing ingestion | remote import stream | the download is bounded and its staged representation is hashed | integration | ❌ |
| 7 | slicer hook characterization | existing ingestion | simple authenticated client | upload completes without browser-only protocol state | integration | ❌ |
| 8 | external-library write-back characterization | existing ingestion | mounted writable library | write-back preserves create-only Artifact publication | integration | ❌ |
| 9 | upload state transitions | state machine | every source and destination state | legal/idempotent transitions pass and illegal transitions fail | unit | ✅ |
| 10 | durable session and parts | persistence | session with several part receipts | owner, envelope, progress, receipts, verification, and result survive reload | integration | ✅ |
| 11 | compare-and-set transition | concurrency | two writers share a version | only one state update succeeds | integration | ❌ |
| 12 | interrupted-state recovery | recovery | process restarts in verifying/ingesting | recovery safely resumes or records a retryable failure | integration | ❌ |
| 13 | fail-closed expiry | cleanup | expired upload without positive ownership | foreign/unproven bytes are retained and a safe failure is reported | integration | ❌ |
| 14 | create and status authorization | RBAC | owner and unrelated user | only the owner or authorized administrator can observe the session | integration | ❌ |
| 15 | target authorization is rechecked | RBAC | Collection access revoked mid-upload | finalize is refused without publishing an Artifact | integration | ❌ |
| 16 | safe status projection | information safety | persisted native identifiers and receipts | response omits paths, credentials, provider ids, and protected values | integration | ❌ |
| 17 | plan response is ephemeral | HTTP caching | request transfer plan | response carries `Cache-Control: no-store` | integration | ❌ |
| 18 | capability-driven plan | adapter selection | native-capable and fallback backends | plan selects by guarantees, never a provider name | unit | ❌ |
| 19 | chunk envelope validation | API chunks | wrong index/offset/length/hash | bytes are rejected before durable progress changes | unit | ❌ |
| 20 | create-only chunk publication | API chunks | valid bounded chunk | temp file is synced and atomically published in the owned directory | integration | ❌ |
| 21 | identical duplicate chunk | API chunks | same chunk uploaded twice | second write succeeds without double-counting bytes | integration | ❌ |
| 22 | conflicting duplicate chunk | API chunks | same index with different bytes | conflict is returned and original receipt remains | integration | ❌ |
| 23 | chunk interruption and restart | API chunks | partial upload then new process/session | status reports receipts and missing chunks can resume | e2e | ❌ |
| 24 | finalize serialization | concurrency | chunk write races finalize | only a complete immutable assembly can enter verification | integration | ❌ |
| 25 | ordered assembly hash | API chunks | complete set of chunks | assembled size and SHA-256 equal the original representation | integration | ❌ |
| 26 | staging capacity admission | limits | pending/byte/free-space limit exceeded | session or chunk is rejected before bytes are accepted | integration | ❌ |
| 27 | scoped native signing | native multipart | allowed and disallowed part requests | only the owned staging key/upload and permitted part range are signed | unit | ❌ |
| 28 | native receipt reconciliation | native multipart | durable receipts differ from remote ListParts | finalize fails safely without trusting client claims | contract | ❌ |
| 29 | native checksum policy | native multipart | target cannot prove required checksum semantics | plan falls back to API chunks | contract | ❌ |
| 30 | exact native abort | cleanup | owned and foreign multipart/object identities | only the positively owned operation is removed | contract | ❌ |
| 31 | native bytes bypass API | transfer accounting | direct multipart browser upload | object bytes do not cross the upload API process | e2e | ❌ |
| 32 | verified staged Artifact | verification | complete staged representation | exact size, SHA-256, immutable identity, and safe materialization are exposed | unit | ❌ |
| 33 | mismatch before ingestion | verification | size/hash/type mismatch | no background job or readable Artifact is created | integration | ❌ |
| 34 | exact ingestion handoff | ingestion | verified model/G-code/revision | normal ingestion receives the verified representation exactly once | integration | ❌ |
| 35 | no partial readability | integrity | upload is created or incomplete | library and download routes expose no Artifact | integration | ❌ |
| 36 | frontend session persistence | frontend resume | page refresh during transfer | only the opaque session id is stored and a fresh plan is fetched | frontend | ❌ |
| 37 | frontend phase reporting | frontend UX | hashing/transfer/verification/ingestion | phases and retryability are shown separately | frontend | ❌ |
| 38 | frontend controls | frontend UX | active, paused, failed, completed upload | pause/resume/cancel/retry controls match server state | frontend | ❌ |
| 39 | browser reload resume | browser E2E | interrupted multipart upload | reload resumes, finalizes, and opens the resulting Model | e2e-real | ❌ |
| 40 | request and rate limits | abuse controls | oversized/frequent create, plan, chunk, finalize calls | bounded requests receive stable 413/429 responses | integration | ❌ |
| 41 | audit and metrics | observability | create/finalize/abort/expiry/failure | safe events and counters report mode, bytes, and outcome | integration | ❌ |
| 42 | stuck-session health | operations | stale active session or cleanup failure | health reports a credential-free actionable finding | integration | ❌ |
| 43 | canonical API contract | OpenAPI | generated schema | only provider-neutral `/artifact-uploads` routes and safe schemas appear | repo | ❌ |
| 44 | documented compatibility | documentation | operator selects a storage transport | modes, limits, CORS, recovery, and fallback behaviour are documented | repo | ❌ |
