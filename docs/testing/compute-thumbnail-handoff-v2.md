# Thumbnail handoff without an encoder child

This is the active handoff implementation. It replaces the retired experiment that spawned a finalizer for every prepared thumbnail. Historical combined-worker and earlier experiment measurements remain in [the pipeline evidence](compute-native-pipeline-200.md); they are not throughput evidence for this implementation. The [final GPU-enabled run of the same 1,000 cached models](compute-final-gpu-1000.md) completed with 973 ready output pairs, the identical 27 invalid sources and no new failures; it did not establish a throughput improvement. No new CPU-render benchmark or R2 request was made for these checks.

## Lifecycle and eligibility

The existing Job keeps source identity and publication authority throughout. Source/output preparation precedes an explicit host-memory reservation and normal conservative native parser admission. The parser writes a bounded, hashed geometry packet into its owned directory; the supervisor validates it only after clean reaping releases native parser credit. GPU success is encoded synchronously in the existing Job thread, under one native CPU slot. There is no encoder child or new thread pool on success.

A live renderer with a completed GPU postprocessor canary must already exist. Qualified mode requires a current device/runtime/recipe receipt; the parser checks its exact face count through the same `Qualification.accepts` policy before emitting a packet. Preview still requires a real ready renderer. Cold/unavailable/unqualified work stays combined and can warm the renderer naturally. Fingerprint requests stay combined so optional analysis cannot delay the earlier basic thumbnail publication.

Only a valid GPU refusal starts a guarded CPU worker from the same packet. It never reloads the source. Invalid input is terminal. The original absolute deadline and cancellation propagate through status, dispatch, codec admission and fallback; neither renews the deadline. A synchronous codec retains its credits until it actually returns even if cancellation was signalled during the call. Output remains subject to the existing attempt/publication fences.

## Resource ownership

Acquisition order is source → host staging → native parser → clean reap/release → GPU → native codec (or guarded CPU fallback). A native permit is never held while acquiring host staging; no claimant needs a resource held behind itself. Each process uses the existing FIFO/priority admission rules. Durable Job concurrency is unchanged.

Let H be known physical/cgroup memory, G the existing native geometry partition and I the configured inference partition. Host staging is at most `min(384 MiB, (H−G−I)/4)` and at most 4 slots, leaving 75% of the otherwise unassigned headroom for the application and OS. Unknown or insufficient capacity retains combined execution. Parser estimates are unchanged. This is explicit application admission, not an OS guarantee that every other process stays within a partition. The broker retains its own existing budget; it does not share or silently consume this host window.

The per-item claim is the maximum of packet plus three bounded raw readback buffers, and a codec phase with 64 MiB base workspace plus 64 bytes/output pixel and three output RGBA buffers. At 640×480 four claims fit 384 MiB on the measured configuration. Packet bytes die before codec entry; codec/output ownership survives until publication leaves scope. Source/output disk preparation still charges two source copies plus the bounded packet, with separate disk free-space claims. Host FD custody is inherited by guarded children. Error tracebacks have completed-frame locals cleared inside the reservation; active frames explicitly release their buffers before host exit.

Codec admission uses `Resources(1,1)` to claim one existing CPU slot; the ledger's mandatory byte is additional to RAM already charged above. This admission is not a process launch. `thumbnail_handoff_complete` emits JSON durations for packet reading, dispatch, codec waiting/encoding and CPU recovery. `thumbnail_handoff_supervision` links the same opaque handoff ID to separate parser/fallback execution IDs and the maximum supervised RSS (never the sum of process peaks). Parent codec RSS is not misreported as a child measurement. No source names or paths appear in these events.

## Handoff coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | accepts the thumbnail layout | Happy | Single transparent default view, fixed dimensions | Valid bounded packet accepted | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestThumbnailPacket::test_accepts_the_thumbnail_layout` |
| 2 | refuses other render contracts | Error | Wrong views, matte, dimensions, mode or counts | Packet rejected before dispatch | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestThumbnailPacket::test_refuses_other_render_contracts` |
| 3 | refuses invalid framing | Error | Missing/truncated/trailing bytes or oversized header | No geometry accepted | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestThumbnailPacket::test_refuses_invalid_framing` |
| 4 | refuses invalid descriptors | Error | Out-of-range size/count or malformed digest | Descriptor construction fails | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestThumbnailPacket::test_refuses_invalid_descriptors` |
| 5 | preserves system headroom | Edge | 8 GiB physical, half geometry, quarter inference | Host limit 384 MiB; four 640px claims fit;75% of remaining headroom untouched | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHostWindow::test_preserves_system_headroom` |
| 6 | refuses unavailable headroom | Edge | Unknown/zero memory or invalid/full partitions | No host capacity is invented | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHostWindow::test_refuses_unavailable_headroom` |
| 7 | covers raw readback before fallback | Edge | Supersampled raw reply | Packet plus three raw-buffer copies remain covered | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHostWindow::test_covers_raw_readback_before_fallback` |
| 8 | retains codec credit until cancellation returns | Error | Codec raises cancellation | Native codec slot held through call and released afterwards; no fallback | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHandoffCompletion::test_retains_codec_credit_until_cancellation_returns` |
| 9 | retains credit while a cancelled codec is still running | Error | Cancellation signalled while codec thread is blocked | Credit remains held until actual codec return, then cancellation propagates | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHandoffCompletion::test_retains_credit_while_a_cancelled_codec_is_still_running` |
| 10 | does not recover terminal failures | Error | Invalid input, deadline or cancellation | No CPU fallback or codec work | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHandoffCompletion::test_does_not_recover_terminal_failures` |
| 11 | clears owned buffers before releasing host credit | Error | Dispatch/fallback/publication errors with cause/context chains; cancellation | Retained buffers collected before host exit while original exceptions retain stack evidence | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestExceptionalCustody::test_clears_owned_buffers_before_releasing_host_credit` |
| 12 | reads a newly written file | Happy | Real packet whose atime changes on read | Stable packet accepted without treating atime as mutation | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_handoff.py::TestPacketCustody::test_reads_a_newly_written_file` |
| 13 | refuses changed custody | Error | Symlink, hardlink, changed bytes or replaced directory | Owned packet rejected | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_handoff.py::TestPacketCustody::test_refuses_changed_custody` |
| 14 | transfers custody after clean reap | Happy | Real source and parser; GPU success or refusal | Native pool available at GPU boundary;1child on success,2only on refusal; fallback succeeds after source deletion; JSON phase/process IDs correlate | Contract | ✅ `backend/tests/contract/modules/media/test_thumbnail_handoff.py::TestPreparedExecution::test_transfers_custody_after_clean_reap` |
| 15 | codec imports no numeric runtime | Edge | Fresh process blocks NumPy/SciPy/Trimesh/WGPU/Cascadio/ORT imports | Packet dispatch and real Pillow encoding succeed | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_handoff.py::TestPreparedExecution::test_codec_imports_no_numeric_runtime` |
| 16 | keeps unqualified geometry in the parser | Edge | Exact face count refused by readiness | Combined rendering returns image; no packet is emitted; original deadline passed | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_handoff.py::TestPreparedEligibility::test_keeps_unqualified_geometry_in_the_parser` |
| 17 | propagates withdrawal during readiness | Error | Readiness raises cancellation or timeout | No packet or fallback rendering | Integration | ✅ `backend/tests/integration/modules/media/test_thumbnail_handoff.py::TestPreparedEligibility::test_propagates_withdrawal_during_readiness` |
| 18 | requires prepared output authority | Error | Prepared frame with/without preparation authority | Only admitted mode accepts it; normal ordering still enforced | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailFrames::test_requires_prepared_output_authority` |
| 19 | refuses published coverage | Error | Prepared descriptor claims published preview | Frame rejected | Unit | ✅ `backend/tests/unit/modules/media/test_mesh_protocol.py::TestPreparedThumbnailFrames::test_refuses_published_coverage` |
| 20 | retains one worker per source | Edge | Combined STL and 3MF requests | One supervised worker; both derivatives published | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_retains_one_worker_per_source` |
| 21 | keeps basics ahead of optional analysis | Edge | Fingerprint requested and later times out | Handoff not selected; thumbnail already durably READY | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_keeps_basics_ahead_of_optional_analysis` |
| 22 | records host admission expiry | Edge | Host wait reaches original deadline | Derivative fails as timeout without parser launch | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_records_host_admission_expiry` |
| 23 | drops result before host release on supersession | Edge | Publication authority superseded after result | Active producer frame releases result before host credit | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_drops_result_before_host_release_on_supersession` |
| 24 | publishes the renderer frame | Edge | Generated valid frame | Published WebP preserves renderer framing | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_publishes_the_renderer_frame` |
| 25 | keeps committed basics after later worker failure | Edge | Failure after basic outputs | Previously committed metadata/thumbnail retained | Integration | ✅ `backend/tests/integration/modules/derivatives/test_producers.py::TestStagedMeshPublication::test_keeps_committed_basics_after_later_worker_failure` |
| 26 | requires prepared source ownership | Edge | No source permit | Host admission refused | Integration | ✅ `backend/tests/integration/runtime/test_preparation_runtime.py::TestThumbnailHostCustody::test_requires_prepared_source_ownership` |
| 27 | refuses reversed native order | Edge | Native permit already held | Host admission refused before creating cycle | Integration | ✅ `backend/tests/integration/runtime/test_preparation_runtime.py::TestThumbnailHostCustody::test_refuses_reversed_native_order` |
| 28 | transports all custody descriptors | Edge | Source, host and native permits | All descriptor identities forwarded; scope clears and credit reusable | Integration | ✅ `backend/tests/integration/runtime/test_preparation_runtime.py::TestThumbnailHostCustody::test_transports_all_custody_descriptors` |
| 29 | keeps credit until inherited descriptor closes | Edge | Duplicated host descriptor outlives parent scope | Capacity stays unavailable until inherited descriptor closes | Integration | ✅ `backend/tests/integration/runtime/test_preparation_runtime.py::TestThumbnailHostCustody::test_keeps_credit_until_inherited_descriptor_closes` |
| 30 | preserves publication across gpu outcomes | Happy | Real ingestion and Jobs; combined/handoff × healthy/device failure/empty frame | Original bytes and exact metadata preserved; thumbnail READY; child count matches route and fallback | E2E | ✅ `backend/tests/e2e/test_render_batch_publication.py::TestThumbnailFallbackPublication::test_preserves_publication_across_gpu_outcomes` |
| 31 | publishes the healthy member when a batched job is cancelled | Error | Two Jobs share GPU fence; one cancelled; small/large geometry | Healthy thumbnail publishes; cancelled member cannot publish late | E2E | ✅ `backend/tests/e2e/test_render_batch_publication.py::TestRenderBatchPublication::test_publishes_the_healthy_member_when_a_batched_job_is_cancelled` |

## Validation

The initial selected run had 292passes and two test-fixture failures: a fake pixel buffer had the wrong length, and the fresh-process probe lacked the backend working directory. Both fixtures were corrected. The expanded parser/engine/protocol/producer/bootstrap/resource/architecture/publication selection then passed **598 tests**, with 2 resource cases deselected and one existing Starlette deprecation warning, in 308.29seconds. The additional custody/producer selection passed **49 tests** in 10.64seconds.

The final affected run reported **179 passed and 2 failed**: stricter readiness exposed a broker-root fixture mismatch and the real socket custody check belonged in the contract tier. The fixture root was corrected and the test moved without weakening assertions. The corrected contract/integration/packet/readiness selection and scoped hygiene then passed **122 tests** in 14.44 seconds (5,230 unrelated hygiene cases deselected). The two real-socket response-limit cases were also moved from unit to contract; their final two-case contract plus scoped hygiene gate passed **14 tests** in 4.41 seconds (5,254 unrelated cases deselected). New topic-named test files were moved under their actual production-module folders. A broader hygiene attempt was interrupted in favor of these affected checks; it is not reported as a complete gate.

Configured `.venv/bin/pyright` reported **0 errors and 0 warnings**; scoped Ruff and `git diff --check` passed. These checks use a controlled qualified renderer through the real broker; they establish lifecycle and publication behavior, not physical GPU performance.

## Packet dispatch and bounded publisher codec

The caller retains prepared-host and codec CPU reservations until these synchronous helpers actually return. These unit checks prove packet/frame/codec behavior; the handoff integration owns clean reaping, physical resource accounting, guarded fallback and durable publication fences. No GPU execution or performance measurement was performed for this helper gate.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | retains immutable packet through exchange | Ownership | Canonical, deferred and visual packets | Same immutable packet owner backs the readonly body; background priority, original deadline, bounded response and exact owned RGBA | Unit | ✅ [`TestPacketDispatch::test_retains_immutable_packet_through_exchange`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 3 cases passed |
| 2 | respects broker deadline ceiling | Boundary | Original deadline farther than 300 seconds | Broker request is clipped to 300 seconds without extending the original deadline | Unit | ✅ [`TestPacketDispatch::test_respects_broker_deadline_ceiling`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 3 | avoids copying the full pixel envelope | Regression | Bytes reply refuses slicing | Exact pixels decode through its buffer without slicing the full response | Unit | ✅ [`TestPacketDispatch::test_avoids_copying_the_full_pixel_envelope`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 4 | owns pixels after the response owner is released | Lifetime | Reply owner collected after dispatch | Owned RGBA remains exact after response destruction | Unit | ✅ [`TestPacketDispatch::test_owns_pixels_after_the_response_owner_is_released`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 5 | accepts one visible pixel | Boundary | Only one pixel has alpha 1 / 128 / 255 | Each nonempty frame is accepted with exact pixels | Unit | ✅ [`TestPacketDispatch::test_accepts_one_visible_pixel`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 3 cases passed |
| 6 | routes unavailable compute to guarded fallback | Fallback | Compute unavailable | Returns None without invoking transport | Unit | ✅ [`TestPacketDispatch::test_routes_unavailable_compute_to_guarded_fallback`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 7 | routes compute refusal to guarded fallback | Fallback | Capacity, device, qualification or broker refusal | Returns None for caller-owned guarded fallback | Unit | ✅ [`TestPacketDispatch::test_routes_compute_refusal_to_guarded_fallback`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 4 cases passed |
| 8 | treats broker input rejection as terminal | Error | Broker INVALID_INPUT | Raises compute_geometry_invalid instead of returning a fallback result | Unit | ✅ [`TestPacketDispatch::test_treats_broker_input_rejection_as_terminal`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 9 | refuses unusable gpu output | Error | Malformed/tag/count/dimensions/length, blank or unfinalized reply | Returns None; no raw CPU postprocessing in the helper | Unit | ✅ [`TestPacketDispatch::test_refuses_unusable_gpu_output`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 9 cases passed |
| 10 | rejects invalid prepared packet before dispatch | Error | Malformed envelope/body, wrong dimensions/camera/matte/shape or mutable backing | Raises before transport receives any packet | Unit | ✅ [`TestPacketDispatch::test_rejects_invalid_prepared_packet_before_dispatch`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 13 cases passed |
| 11 | propagates broker withdrawal | Withdrawal | Broker DEADLINE or CANCELLED | TimeoutError or OperationCancelled propagates instead of fallback | Unit | ✅ [`TestPacketWithdrawal::test_propagates_broker_withdrawal`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 2 cases passed |
| 12 | refuses expired work before dispatch | Withdrawal | Expired absolute deadline | No transport; thumbnail_deadline raised | Unit | ✅ [`TestPacketWithdrawal::test_refuses_expired_work_before_dispatch`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 13 | refuses cancelled work before dispatch | Withdrawal | Already withdrawn Job | No transport; OperationCancelled raised | Unit | ✅ [`TestPacketWithdrawal::test_refuses_cancelled_work_before_dispatch`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 14 | withdraws pixels completed after the deadline | Withdrawal | Deadline expires during exchange | Late pixels are not returned | Unit | ✅ [`TestPacketWithdrawal::test_withdraws_pixels_completed_after_the_deadline`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 1 case passed |
| 15 | withdraws pixels after cancellation during dispatch | Withdrawal | Cancellation during successful or refused exchange | OperationCancelled propagates in both cases | Unit | ✅ [`TestPacketWithdrawal::test_withdraws_pixels_after_cancellation_during_dispatch`](../../backend/tests/unit/modules/media/compute_render/test_packet.py), 2 cases passed |
| 16 | bounds the supersampled reply before reading | Boundary | Real client over socket pair; raw supersampled envelope at limit or one byte larger | At-limit raw response selects fallback; larger declared payload is rejected before its bytes are sent | Contract | ✅ `backend/tests/contract/modules/media/compute_render/test_packet.py::TestPacketReplyLimit::test_bounds_the_supersampled_reply_before_reading`, 2 cases passed |
| 17 | preserves all rgba bytes | Happy | 320 px and 1280 px frames with alpha 1 and hidden RGB | Decoded WebP retains every RGBA byte and dimensions | Unit | ✅ [`TestEncodePixels::test_preserves_all_rgba_bytes`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 2 cases passed |
| 18 | uses the existing lossless encoding recipe | Compatibility | Valid canonical frame | Actual Pillow save receives WEBP lossless/exact, method 2 and quality 80 | Unit | ✅ [`TestEncodePixels::test_uses_the_existing_lossless_encoding_recipe`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 1 case passed |
| 19 | refuses foreground at the frame border | Boundary | Alpha 1 on each of four borders | Refuses frame before codec call | Unit | ✅ [`TestEncodePixels::test_refuses_foreground_at_the_frame_border`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 4 cases passed |
| 20 | refuses an empty frame | Error | Fully transparent frame | Raises thumbnail_empty | Unit | ✅ [`TestEncodePixels::test_refuses_an_empty_frame`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 1 case passed |
| 21 | refuses dimensions outside the thumbnail contract | Boundary | Width 319 / 1281 or wrong aspect ratio | Raises thumbnail_frame_invalid | Unit | ✅ [`TestEncodePixels::test_refuses_dimensions_outside_the_thumbnail_contract`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 3 cases passed |
| 22 | preserves codec failure | Error | Pillow raises an encoding failure | Error propagates; no successful result is returned | Unit | ✅ [`TestEncodePixels::test_preserves_codec_failure`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 1 case passed |
| 23 | refuses cancellation before encoding | Withdrawal | Already withdrawn Job | Codec is not called | Unit | ✅ [`TestEncodingWithdrawal::test_refuses_cancellation_before_encoding`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 1 case passed |
| 24 | refuses invalid deadlines before encoding | Withdrawal | Expired, infinite or NaN deadline | Codec is not called; thumbnail_deadline raised | Unit | ✅ [`TestEncodingWithdrawal::test_refuses_invalid_deadlines_before_encoding`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 3 cases passed |
| 25 | withdraws output after cancellation during encoding | Withdrawal | Cancellation inside synchronous codec call | Completed output is withheld after the call returns | Unit | ✅ [`TestEncodingWithdrawal::test_withdraws_output_after_cancellation_during_encoding`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 1 case passed |
| 26 | withdraws output completed after the deadline | Withdrawal | Deadline expires inside synchronous codec call | Completed output is withheld after the call returns | Unit | ✅ [`TestEncodingWithdrawal::test_withdraws_output_completed_after_the_deadline`](../../backend/tests/unit/modules/media/test_thumbnail_pixels.py), 1 case passed |

All 64 new cases passed together with 20 existing render cases and the single-behaviour test-name hygiene check: **85 passed in 9.42 seconds**. Ruff format/check and scoped Pyright are clean. no GPU diagnostic or throughput claim is implied.


## Readiness coverage

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | accepts only existing qualified owner | Edge | Current receipt at valid boundary | Existing live owner accepted | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_accepts_only_existing_qualified_owner` |
| 2 | retains unmeasured models in combined worker | Edge | Face count outside measured interval | Handoff refused | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_retains_unmeasured_models_in_combined_worker` |
| 3 | rejects stale receipts | Edge | Wrong device/runtime/recipe, inference-only or failed quality | Handoff refused | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_rejects_stale_receipts` |
| 4 | requires current receipt contents | Edge | Missing, corrupt or empty receipts | No qualification inferred | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_requires_current_receipt_contents` |
| 5 | requires live final pixel capability | Edge | Preview/qualified owner missing device, capability or postprocessor | Combined work retained | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_requires_live_final_pixel_capability` |
| 6 | preview requires no receipt | Edge | Ready preview device | No receipt required by preview policy | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_preview_requires_no_receipt` |
| 7 | disabled dispatch skips owner probe | Edge | Compute disabled | No owner started or queried | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_disabled_dispatch_skips_owner_probe` |
| 8 | rejects invalid units | Edge | Nonpositive or malformed units | Invalid request rejected | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_rejects_invalid_units` |
| 9 | cancellation never becomes combined work | Edge | Cancellation before readiness | Cancellation propagates | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_cancellation_never_becomes_combined_work` |
| 10 | expired deadline never probes | Edge | Expired deadline | No status request | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_expired_deadline_never_probes` |
| 11 | preserves deadline at owner probe | Edge | Future absolute deadline | Same deadline and nonstarting probe passed | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_preserves_deadline_at_owner_probe` |
| 12 | withdrawal during receipt read propagates | Edge | Cancellation during receipt read | No combined fallback | Unit | ✅ `backend/tests/unit/runtime/compute/client/test_readiness.py::TestRenderReady::test_withdrawal_during_receipt_read_propagates` |
| 13 | nonstarting probe never launches owner | Edge | Missing broker | No owner or launch lock created | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_nonstarting_probe_never_launches_owner` |
| 14 | status uses bounded original deadline | Edge | Requested deadline | Five-second ceiling never extends original | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_status_uses_bounded_original_deadline` |
| 15 | expired status does not connect | Edge | Already expired | No socket connection | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_expired_status_does_not_connect` |
| 16 | deadline during receive propagates | Edge | Deadline expires in receive | Timeout propagated | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_deadline_during_receive_propagates` |
| 17 | completed late reply is withheld | Edge | Reply after deadline | Late status rejected | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_completed_late_reply_is_withheld` |
| 18 | probe timeout does not extend original deadline | Edge | Probe timeout | Original deadline unchanged | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_probe_timeout_does_not_extend_original_deadline` |
| 19 | cancellation during receive propagates | Edge | Cancellation in receive | Cancellation propagated | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_cancellation_during_receive_propagates` |
| 20 | default status retains startup behavior | Edge | Default status call | Existing startup behavior retained | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_default_status_retains_startup_behavior` |
| 21 | exchange retains request priority startup | Edge | Urgent/nonurgent request | Original startup policy retained | Unit | ✅ `backend/tests/unit/runtime/compute/test_client.py::TestStatus::test_exchange_retains_request_priority_startup` |

`pytest tests/unit/runtime/compute/test_client.py tests/unit/runtime/compute/client/test_readiness.py tests/unit/modules/media/test_compute_render.py tests/unit/modules/media/compute_render/test_packet.py -q --tb=short` — **131 passed**. This includes shared render-recipe identity and legacy dispatch behavior. No GPU work was performed.

## Cancellation polling regression

Transport callbacks and admission polls now reuse the existing 200 ms Job cancellation probe. Every callback still checks the original absolute monotonic deadline. Forced cancellation checks remain at GPU entry/completion/refusal, after host or native admission, before and after the synchronous codec, and before guarded fallback launch and after clean reap. They also run before handing a completed result back to publication. The transactional publication fences are unchanged.

The previous helper forced a withdrawal query for every transport chunk and admission poll. The correction removes that repeated-query amplification; it does not establish an end-to-end speedup. Cancellation during the native codec still retains host and CPU credit until the codec actually returns. The fresh complete 1,000-source run retained all expected outcomes; its longer historical comparison uses a different policy and does not establish a speedup.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | limits withdrawal reads during repeated polls | Regression | Real cancellation scope; 200 callbacks before 200 ms elapse | One withdrawal read; a second read occurs at 210 ms | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_pixels.py::TestDeadlinePolling::test_limits_withdrawal_reads_during_repeated_polls` |
| 2 | deadline expires between cancellation probes | Deadline | 50 ms deadline checked 60 ms after the first probe | Timeout propagates without waiting for another withdrawal query | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_pixels.py::TestDeadlinePolling::test_deadline_expires_between_cancellation_probes` |
| 3 | boundary observes cancellation inside the poll interval | Withdrawal | Cancellation arrives before the next scheduled probe | Forced phase boundary raises immediately | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_pixels.py::TestDeadlinePolling::test_boundary_observes_cancellation_inside_the_poll_interval` |
| 4 | fragmented transport reuses the cancellation probe | Regression | Real transport framing with an in-memory connection and more than 500 fragments | Fragment callbacks make no additional withdrawal reads; the result boundary forces one read | Unit | ✅ `backend/tests/unit/modules/media/compute_render/test_packet.py::TestPacketPolling::test_fragmented_transport_reuses_the_cancellation_probe` |
| 5 | codec admission polls reuse the cancellation probe | Withdrawal | 100 admission polls; cancellation absent or arriving on acquisition | Polls make no additional withdrawal reads; cancelled acquisition never calls codec; credit released | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHandoffCompletion::test_codec_admission_polls_reuse_the_cancellation_probe`, 2 cases |
| 6 | host acquisition forces cancellation before yield | Withdrawal | 100 host polls; cancellation arrives as credit is acquired | One poll query, then forced cancellation before yielding; credit released | Unit | ✅ `backend/tests/unit/modules/media/test_thumbnail_handoff.py::TestHostAdmissionPolling::test_host_acquisition_forces_cancellation_before_yield` |
| 7 | withholds withdrawn fallback at phase boundary | Withdrawal | Cancellation after native admission or after guarded fallback clean reap | No cancelled launch or returned image; original deadline and credit custody retained | Unit | ✅ `backend/tests/unit/modules/media/mesh_isolation/test_fallback_cancellation.py::TestFallbackCancellation::test_withholds_withdrawn_fallback_at_phase_boundary`, 2 cases |

The focused selection passed **112 tests in 17.58 seconds**, including the existing GPU reply/refusal cancellation checks and the blocked-codec custody check now using a real cancellation scope. Scoped Ruff was clean and Pyright reported **0 errors** for the four changed production files. These counts overlap earlier selections; no GPU workload or performance measurement was run for this regression gate.

```bash
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_thumbnail_pixels.py \
  tests/unit/modules/media/compute_render/test_packet.py \
  tests/unit/modules/media/test_thumbnail_handoff.py \
  tests/unit/modules/media/mesh_isolation/test_fallback_cancellation.py \
  tests/integration/modules/media/test_thumbnail_handoff.py -q -n0 --tb=short
```

## Reproduction commands

Run pytest selections serially from `backend`; do not overlap a throughput measurement.

```bash
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/unit/modules/media/test_thumbnail_handoff.py \
  tests/contract/modules/media/test_thumbnail_handoff.py \
  tests/integration/modules/media/test_thumbnail_handoff.py \
  tests/unit/modules/media/test_mesh_protocol.py \
  tests/integration/modules/derivatives/test_producers.py \
  tests/integration/modules/media/test_thumbnail_engine.py \
  tests/integration/modules/media/test_mesh_isolation.py \
  tests/unit/modules/media/test_worker_bootstrap.py \
  tests/integration/modules/media/test_worker_bootstrap.py \
  tests/integration/runtime/test_preparation_runtime.py \
  tests/repo/test_architecture.py tests/repo/test_mesh_boundaries.py \
  tests/e2e/test_render_batch_publication.py -q -n0 --tb=short \
  -m 'not slow and not coverage_gate and not scale and not postgres and not s3 and not remote_storage and not bgcode and not native_host'
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/contract/modules/media/test_thumbnail_handoff.py \
  tests/integration/modules/media/test_thumbnail_handoff.py \
  tests/unit/modules/media/compute_render/test_packet.py \
  tests/unit/runtime/compute/client/test_readiness.py \
  tests/repo/test_test_hygiene.py \
  -k 'thumbnail_handoff or test_packet or test_readiness' -q -n0 --tb=short
PRINTSTASH_TEST_NO_EXTERNAL=1 .venv/bin/python -m pytest \
  tests/contract/modules/media/compute_render/test_packet.py \
  tests/repo/test_test_hygiene.py -k test_packet -q -n0 --tb=short
.venv/bin/pyright
```

The first command shows the current paths for the expanded regression selection; the recorded 598-case run preceded the readiness-related test moves. Counts from overlapping selections are not added into one claimed test total. The final 1,000-source deployment evidence is recorded separately in [the complete result](compute-final-gpu-1000.md). All 17 CI checks passed for the measured code commit `fd76e2de`; documentation-only follow-up CI is tracked on the PR.
