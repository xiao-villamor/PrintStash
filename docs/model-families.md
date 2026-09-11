# Model Families

Model Families record variations you have chosen to keep together. Each member
remains an independent Model with its own source files, G-code Revisions, tags,
print history and known-good status. A Family is a relationship, not a combined
printable file or a Multipart Model.

## Create and browse

Select Models in the library and choose **Create Family**, or start from an
individual Model. Search and load more results without losing your selection.
Choose the canonical Model explicitly before creating the Family. It is the
reference variation and the default Model offered for printing.

Use **Group variations** to switch between individual Models and one card per
matching Family, alongside ungrouped Models. Membership and variation filters
can be saved in Saved Views. Family name and description search also works;
member counts and cover choices reflect your current access.

The detail page shows source files, Revisions, known-good counts and the latest
print result for each member. Filter or sort this list before comparing or
editing a variation. A Family's own tags, Collection, star and cover are separate
from those of its members.

## Variations and comparison

The human-assigned roles are Identical, Rescaled, Mirrored, Repaired and Print
variant. Notes, a known scale factor and verified mirror information describe
the relationship to a reference Model; they do not modify any geometry.

Select exactly two members to compare their geometry and print metadata. The
previews share a physical scale and camera, so resizing differences remain
visible. STL and OBJ do not declare units; the comparison says when units are
unknown. Unsupported or failed previews retain their metadata.

Changing the canonical Model requires choosing the previous canonical's role.
Known relative measurements can be rebased; uncertain relationships need review.
Removing the canonical member leaves an explicit vacancy. No member is promoted
automatically, and printing is unavailable until a usable canonical Model exists.

## Membership and printing

A Model reserves membership in one active Family, including while the Model is
in trash. Moving it between Families requires reviewing both groups and
confirming one atomic move. Shared edits require edit access to the Family's
Collection and every reserved member; read-only users still see authorized data.

**Print canonical Model** uses the existing printer and Revision selection flow.
Bulk member actions explicitly tag, move or star the indicated Models. They do
not propagate Revisions or known-good status, and there is no bulk member trash
action on a Family.

In a Multipart editor, **Add Family variations as Choices** lets you select
specific visible siblings. Existing Choices are excluded. The selection changes
the local draft; **Save** persists it and rechecks access. Later Family edits do
not silently alter that Multipart composition.

## Trash, backup and transfer

Trashing or permanently deleting a Family preserves its Models and files.
Restoration reacquires surviving memberships together; a conflicting Family
membership prevents partial restoration. Permanently deleted Models are reported
as omissions.

Portable library archive v2 includes Families, relationship metadata, covers and
Family-based Saved Views. It uses stable identities across installations.
Reimporting the same Family preserves local edits and canonical choices. A
membership conflict skips the incoming Family and is reported. Legacy v1 remains
readable; explicitly exporting v1 warns that Families and their Saved Views are
omitted. Database backups preserve live and trashed Families and owned covers.

Manual Families work without Similar Models or inference/search packages.
Similarity review does not automatically create, merge or classify Families.
