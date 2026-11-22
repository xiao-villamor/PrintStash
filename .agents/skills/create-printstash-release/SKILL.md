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

## Identify the requested boundary

| Request | Deliverable |
| --- | --- |
| Readiness assessment | Proposed version, merged scope, current `main` SHA, CI state, upgrade and store prerequisites |
| Release preparation | Metadata-only change through [deliver-printstash](../deliver-printstash/SKILL.md), with all version sites and catalogue inputs checked |
| Publish | One integrated commit with successful CI and Deep CI, matching tag, GitHub release, and published image evidence |
| Update a store | Verified release image digest, repository-owned package, and the authorized store-specific procedure |

Use the [catalogue guide](../../../catalogues/README.md) for submission
details. Read [UPGRADE.md](../../../UPGRADE.md) for upgrade and recovery.
Keep supported compatibility and hardware evidence accurate in their
maintained docs. If an upstream check, permission, or required external
validation is missing, identify the blocked step and the verified inputs.

On resume inspect the tag, checks, GitHub release, image manifests,
and store PRs before repeating writes. Reuse evidence only for the exact
commit and image that will ship. Keep task-owned release evidence private
under `reports/`.
