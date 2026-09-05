# Manufacturing multipart models

This feature is in `Unreleased` and will ship in a minor release.

A Multipart Model describes a composition. Give each Part its quantity per
object, then choose **Create build** to record a particular manufacturing run.
Existing parts start at one. A build copies the composition, quantities and Model
Choices; later composition edits or deletion do not rewrite its history.

Choose how many objects to make and select a G-code Revision for each piece.
A recommended Revision is only an initial selection. Changing a recommendation
later does not change the build. You can save a build without a Revision and
select it when you are ready to print. Inaccessible Models or Revisions appear
as unavailable; historical quantities and results remain.

## Queue pieces and confirm results

Specify how many pieces of this one type the selected file produces. PrintStash
proposes enough jobs for the missing units, excluding units already active or
awaiting review. Confirm any excess explicitly before queuing. Each job belongs
to one build piece; plates mixing several piece types are outside this workflow.

A completed print suggests that all planned pieces are usable. A failed or
cancelled print suggests zero. Inspect the output and confirm or correct that
number: job completion alone never increases the usable count.

For four required legs, confirming three usable pieces leaves one missing.
Queueing its replacement creates another job. Confirming one usable replacement
completes the piece and keeps the earlier attempt and its result. Generic job
retry cannot reset a linked physical attempt. Result confirmations use version
checks and durable request IDs so a repeated request cannot double-count output.

Build access follows the original collection's permissions. Queuing also requires
access to the selected Model and permission to print on the selected printer.
Automatic routing is available to administrators. Archiving keeps history;
unarchive a build to edit it again. Duplicating copies its configuration with no
jobs or confirmed output. Database backups include build history and confirmations.
