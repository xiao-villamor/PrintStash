---
name: create-printstash-release
description: >
  Cut or publish a PrintStash release, prepare a version bump, or check readiness of an explicitly requested release.
---

# Create a PrintStash release

Check GitHub Releases and [CHANGELOG.md](../../../CHANGELOG.md) to establish
the last shipped version and what is Unreleased. Follow the
[release procedure](references/release.md) in order. It owns the integrated
readiness gate, version and changelog updates, exact-SHA CI, and store updates.

For a readiness-only request, report the gate results. For an authorized
release, verify CI and Deep CI on the final merged commit before tagging.
Verify the tag, release and published images refer to that commit. Follow
the store-specific authorization and validation steps in the procedure.
