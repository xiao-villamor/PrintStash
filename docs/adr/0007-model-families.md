# Preserve Model identity through Family relationships

Families describe variations of a design while Multipart Models describe
composition. A Family owns relational metadata, never its members' Artifacts,
Revisions, provenance or print history. Manual workflows and portable archives
remain independent of geometry analysis and inference.

An active membership is unique per Model, including a trashed Model. Trashing a
Family archives its memberships; restoring it either reacquires every surviving
membership or fails atomically. Purged Models leave detached history without a
Model reference, allowing restore to report the omission without inventing data.
A global uniqueness constraint on all historical memberships would prevent both
regrouping and faithful restoration.

The canonical representative is a human selection. Vacancy is allowed after
detach or purge, and a trashed canonical Model reserves its selection until a
person replaces it. Automatic promotion would silently assign meaning to a
variant and could change which Model a printing action uses. Relative scales
are rebased only from a known common reference; mirror relationships without
that evidence retain their provenance and require review.

Portable manifest v2 carries Families identified by a stable export UUID and
members identified by content hash. The reader retains v1 support; users can
explicitly export v1 without Families for older installations. Slug collisions
never merge identities, and reimport never replaces a local canonical selection
or moves a member out of another Family.
