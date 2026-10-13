# Keep library navigation stable until an explicit refresh

Status: Accepted direction; implementation pending.

The library will preserve its visible ordering when external changes arrive and
announce that newer results are available; continuation must refresh first when
membership or ordering has changed. This avoids moving the reader's place without
introducing server-side historical snapshots: continuation needs a server-validated
browse revision, while confirmed local mutations remain immediately visible and
permission loss takes precedence over retaining old content.

The cost is an explicit refresh before further pagination after relevant changes.
A keyset cursor alone cannot guarantee this policy when existing rows can move.
See the [frontend architecture](../architecture/frontend.md) for the distinction
between browse revisions and edit versions.
