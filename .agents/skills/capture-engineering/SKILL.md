---
name: capture-engineering
description: >
  Implement or review PrintStash Pending Imports, provenance, URL capture, capture provider connections, or the browser extension.
---

# Capture engineering

Read the [capture reference](references/capture.md) for ownership and
validation across the extension, HTTP adapters, staging leases, ingestion, and
Library Source data. Follow the relevant engineering skill for each changed
layer and [frontend-backend integration](../frontend-backend-integration/SKILL.md)
for the shared contract.

Verify the supported caller flows with the prescribed tests. Account for
staging ownership, cancellation, retries, and the persisted provenance that
remains after import.

## Trace the handoff

Start at the actual caller: direct URL, extension, provider connection,
reviewed Pending Import, or a portable archive. Identify which input is
trusted, who can submit and review it, how the source is identified, and
who owns captured bytes at each stage. The provider adapter owns extraction
or resolution, the transport owns communication, and the ingestion
capability owns durable import and staging transitions.

For a change involving staged bytes, walk before upload, reviewed or partial
upload, transfer to import, commit, retry, expiry, and cancellation. Retain
the owner and evidence needed by each cleanup path. Losing a response or
repeating a finalization must preserve a coherent disposition of the bytes.
Check authorization or ownership again at the receiving boundary.

## Constrain the contract

Preserve bounded portable input and normalize foreign data at its adapter.
Keep provenance separate from transport credentials and transient URLs.
Keep provider-specific parsing out of the popup and common transport.
Preserve supported extension/server and archive versions, test every
changed caller, and update provider support claims only with appropriate
evidence. Use [create-tests](../create-tests/SKILL.md) for staging,
ownership, failure, and compatibility scenarios.
