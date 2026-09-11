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

Reimport treats an existing export UUID as an already-applied Family, retaining
its local metadata, cover, member edits and trash state as well as its canonical
selection. A conflict skips the complete incoming Family; independently imported
Models remain available. Missing references are reported without inventing a
representative.

Saved Views also carry the stable Family identity and resolve its destination ID
after import. If the target is absent, import reports and skips that view rather
than broadening its filter. Legacy v1 export omits Family-filtered Saved Views
along with Families and explains both omissions before download.

Uploaded covers use the stable UUID as their storage namespace. Their owner can
publish a bounded, normalized image with a durable creation receipt before a new
Family has a database ID, then commit membership, metadata and ownership together.
Failure compensates only the exact newly created image. The shared ownership
census includes both live and trashed Family covers for backup and vault migration.

Application composition installs optional Model annotations and mesh derivatives
through explicit library and ingestion ports. The library owner does not import
Similarity or inference/search code. When either related package is absent,
composition omits its routes and scheduler; manual ingestion, Family identity,
search, Saved Views, Multipart Choices and portable transfer remain available.
The normal installation still installs its annotations when analysis is disabled,
so existing review badges and filters keep their established behavior.
