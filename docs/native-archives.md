# Native archive inspection and extraction

PrintStash validates and expands import ZIP files in Rust while retaining the
existing `printstash_core.files` API. The native implementation is a format
module: it does not know about HTTP, SQLAlchemy, jobs, Artifacts, users, or
storage providers.

## Ownership

| Concern | Owner |
| --- | --- |
| ZIP decoding, path policy, limits, CRC verification, and bounded copying | Rust `printstash-archive-core` |
| Typed translation and temporary-file lifetime across generator yields | `printstash_core.files.archives` |
| Import coordination | Existing Python import pipeline |
| Durable execution | Existing `BackgroundJob` queue |
| Artifact publication | Existing ingestion transaction |

Inspection and extraction use `zip` 8.6.0 with its maintained Deflate, Bzip2,
and LZMA implementations. Unicode paths use `unicode-normalization` 0.1.25 and
`unicode-casefold` 0.2.0. Staged bytes use `tempfile` 3.27.0 and a create-only
publish operation, so an archive entry never replaces an existing destination.
All versions are exact in `Cargo.lock`.

The Python boundary transfers paths, limits, entry descriptors, and opaque
staging names. Archive bytes do not cross PyO3. Native work releases the GIL.
Eager extraction remains all-or-nothing. Incremental extraction keeps one
expanded entry alive and deletes it when the consumer advances, closes, raises,
or exhausts the iterator.

## Preserved contracts

- Entry count includes directory records.
- Central-directory, entry, total expansion, UTF-8 path, and depth limits are
  checked before extraction.
- NFC normalization plus full non-Turkic Unicode case folding rejects names
  that would collide on common filesystems.
- Absolute paths, Windows drive paths, traversal, directory-as-file names, and
  symbolic-link records are rejected.
- Unsupported selected entries are ignored and duplicate selections run once.
- Entry identity remains `index:crc:size`; inspection preserves archive order
  and the original entry name.
- Extraction reopens the current archive, revalidates the selected entry,
  reads through EOF for CRC verification, counts actual output bytes, writes a
  private sibling file, and publishes without replacement.

## Coverage matrix

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | lists a supported model file with its type | Happy | ZIP containing nested STL | Stable entry descriptor identifies the STL | Core | ✅ `TestInspectArchive::test_lists_a_supported_model_file_with_its_type` |
| 2 | lists an image without a file type | Happy | ZIP containing PNG | Entry is selectable as an image | Core | ✅ `TestInspectArchive::test_lists_an_image_without_a_file_type` |
| 3 | refuses an entry that could escape the extraction root | Error | Traversal, absolute, and drive-prefixed names | `archive_unsafe_entry` | Core | ✅ `TestInspectArchive::test_refuses_an_entry_that_could_escape_the_extraction_root` |
| 4 | refuses two entries that normalize to one name | Error | NFC/NFD duplicate | `archive_duplicate_entry` | Core | ✅ `TestInspectArchive::test_refuses_two_entries_that_normalize_to_one_name` |
| 5 | refuses two entries that full casefold to one name | Error | `Maße` and `MASSE` | `archive_duplicate_entry` | Core | ✅ `TestInspectArchive::test_refuses_two_entries_that_full_casefold_to_one_name` |
| 6 | refuses a symbolic link entry | Error | ZIP Unix symlink record | `archive_unsafe_entry` | Core | ✅ `TestInspectArchive::test_refuses_a_symbolic_link_entry` |
| 7 | refuses declared expansion beyond configured limits | Error | Oversized entry, aggregate, directory, path, or depth | Stable limit-specific error code | Core | ✅ `TestInspectArchiveLimits` and `TestInspectArchive` limit cases |
| 8 | stages the bytes of a selected entry | Happy | Selected supported entry | Exact bytes appear under the opaque staging name | Core | ✅ `TestExtractSelected::test_stages_the_bytes_of_a_selected_entry` |
| 9 | preserves an existing staging destination | Error | Generated destination already exists | Existing bytes remain unchanged | Core | ✅ `TestExtractSelectedFailures::test_preserves_an_existing_staging_destination` |
| 10 | removes everything staged when one entry is refused | Error | Valid entry followed by oversized entry | Staging directory is empty | Core | ✅ `TestExtractSelectedFailures::test_removes_everything_it_staged_when_one_entry_is_refused` |
| 11 | expands only one entry at a time | Edge | Two selected members | First file is deleted before second is yielded | Core | ✅ `TestIncrementalExtraction::test_expands_only_one_entry_at_a_time` |
| 12 | cleans incremental extraction after exhaustion | Edge | Repeated selection of one member | One result and empty staging directory | Core | ✅ `TestArchivesContract::test_incremental_extraction_cleans_up_after_exhaustion` |
| 13 | imports an archive through the application flow | Happy | Upload followed by selection | Expected Artifacts and terminal job state | E2E | ✅ `tests/e2e/test_ingest.py` archive flows |

Focused evidence on the implementation revision: five Rust core tests and 53
Python archive-contract tests pass. Full application, packaging, native
coverage, container, security, and controlled-performance gates remain required
on the milestone PR revision.

## Performance protocol

The controlled comparison uses release wheels and the existing import harness.
It compares the immediate parent and M00 baseline under 2 CPU/2 GiB and 4
CPU/4 GiB profiles. The corpus covers many small members, stored and compressed
large members, deep accepted paths, and bounded rejection cases. Source hashes,
selected entry identities, extracted hashes, error codes, and cleanup state must
match before timing is accepted. Report inspection and extraction latency,
throughput, CPU, RSS, and container peak memory; the milestone is blocked by a
repeatable regression beyond the project thresholds.
