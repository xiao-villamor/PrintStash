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
