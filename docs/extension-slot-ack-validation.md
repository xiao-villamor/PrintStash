# Extension slot acknowledgement validation

Malformed upload acknowledgements now dismiss the acknowledged unfinished capture before
surfacing a stable validation error. Authentication retirement remains a separate boundary.

## Exact manifest

- browser-extension/capture-transport.ts
- browser-extension/tests/capture-transport.test.ts
- docs/extension-slot-ack-validation.md (new)

No popup/core/provider/backend/manifest/dependency changes. No new server endpoint,
request nonce, storage owner, or cross-popup coordinator.

## Receipt and ownership rule

An owned receipt is the positive safe-integer item.id parsed from the JSON body of
this invocation's successful slot-creation POST, received through its existing
capture stage lifetime. The transport retains that id beside the invocation's immutable vault and authorization
values before validating the slots array, then enters the existing bounded dismissal
scope immediately after accepting the receipt.

Separate two outcomes:

1. No readable JSON, no object item, or missing/invalid/non-positive/unsafe item.id:
   no owned receipt. Reject without guessing an id or issuing DELETE.
2. Owned receipt, but invalid slots: reject through the existing cleanup scope,
   DELETE exactly that receipt's item id using the original vault/credential,
   and preserve the original validation error if cleanup fails or times out.

A successful server acknowledgement is the existing protocol's ownership evidence.
The backend additionally requires exact owner, browser source kind and CAPTURED
state before dismissal. This does not cryptographically prove that a corrupt server
has not returned the id of another same-owner pending capture: the protocol has no
client request nonce. Requiring the full echoed source/manifest would change the currently consumed receipt
contract and is outside this checkpoint.

## Parsing seam

The transport parses the unknown response into a receipt first and validates slots
inside cleanup. Before any PUT it validates all consumed fields: array/count,
non-null object members, nonempty unique string slot ids, closed role, nullable/string
source-file id, plus exact filename/media type/byte size/hash match to each
submitted declaration. The complete upload plan matches by source-file identity,
preserving reordered replies. UUID spelling and unused state fields are unrestricted:
current contracts and fixtures accept slot-a and omit state. Additional response fields
stay additive. Invalid slots produce one stable error before constructing upload URLs.
The final-result schema is unchanged.

## Requirement matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|----------------------|----------|----------------------|-----------------------------|------|--------|
| 1 | dismisses owned receipts with malformed slot lists | Error | Positive item id; missing/null/non-array/wrong-count slots | One DELETE of owned item; no PUT/finalize; stable validation error | Unit | ✅ capture-transport.test.ts |
| 2 | dismisses owned receipts with malformed slot members | Error | Null member, missing/blank id, invalid role, mismatched declaration fields | Same owned cleanup; no undefined upload URL | Unit | ✅ capture-transport.test.ts |
| 3 | rejects uncertain receipts without dismissal | Error | Missing/null item; missing/string/nonpositive/unsafe id | No DELETE; no PUT/finalize | Unit | ✅ capture-transport.test.ts |
| 4 | rejects unreadable acknowledgement bodies without dismissal | Error | Invalid JSON | No speculative DELETE | Unit | ✅ capture-transport.test.ts |
| 5 | preserves validation failure when cleanup fails | Error | Owned malformed ACK; DELETE rejects or returns403/500 | Original invalid-slots error preserved | Unit | ✅ capture-transport.test.ts |
| 6 | bounds malformed-ACK dismissal | Error | Cleanup ignores abort indefinitely | Timeout aborts cleanup; original validation error surfaces | Unit | ✅ capture-transport.test.ts |
| 7 | binds malformed-ACK dismissal to its original connection | Edge | Original vault/credential supplied; malformed ACK id44 | DELETE only original URL/id and credential | Unit | ✅ capture-transport.test.ts |
| 8 | ignores late creation acknowledgements after retirement | Edge | Held headers/body completes after signal retirement | No guessed cleanup, upload or finalize | Unit | ✅ capture-transport.test.ts |
| 9 | accepts reordered valid slot acknowledgements | Happy | Existing file declarations, reversed slots | Exact matched uploads and successful finalize | Unit | ✅ browser-extension/tests/capture-transport.test.ts::preserves provider IDs when selected files are reordered and slots arrive out of order |
| 10 | accepts a mixed file and cover acknowledgement | Happy | File and cover receipts; cover source id is null | Both slots uploaded and capture finalized | Unit | ✅ capture-transport.test.ts |
| 11 | rejects reused upload slot identities | Error | File and cover receipts reuse one slot id | Owned dismissal before any PUT | Unit | ✅ capture-transport.test.ts |

## Validation evidence

The new receipt matrix ran before production edits: 14 failures and 21 passes
(17 existing tests filtered). A separate reused-slot-id case also failed, resolving
successfully after uploading the same slot twice. Failures demonstrated skipped
cleanup, null-member errors and invalid upload URLs; they were not harness failures.

After the fix, all 53 transport tests passed, including the 36 new cases. Existing
coverage retains authentication classification, transfer cleanup and reordered slots.
The transport fixtures use real Response JSON parsing, one-byte Blob payloads and
fake timers only for the bounded cleanup deadline.

Final extension formatting, lint and type checks passed. Chrome, Firefox and Edge
builds passed, followed by 249 tests across 13 files (8.67 seconds). The fresh-backend
capture contract remains pending. No new native browser case is needed for this JSON transport boundary;
this checkpoint makes no new native-browser execution claim.

## Limits

A cancelled or timed-out creation response that has not supplied an accepted receipt
cannot be safely dismissed. Failed dismissal preserves the original error, and the
unfinished item may require manual dismissal. Cleanup uses the existing five-second
limit. This change introduces no server correlation nonce or cross-popup transaction.
