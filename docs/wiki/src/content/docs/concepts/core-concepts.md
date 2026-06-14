---
title: Core concepts
description: Models, artifacts, revisions, collections, and trash — and how they relate.
---

PrintStash has a small, deliberate vocabulary. Getting it straight up front makes
the rest of the app (and this wiki) much easier to follow, because the same words
mean the same things everywhere — in the UI, the API, and the code.

## Model

A **model** is a *logical* asset, not a file. It's "the thing you printed" — a
bracket, a vase, a Benchy — and it owns all the files that belong to it: the
source mesh, every G-code revision, the thumbnail, the metadata.

Models are deduplicated by the SHA-256 of their source mesh. Upload the same STL
twice and you get one model, not two. That's what keeps the library from filling
up with near-duplicates every time you re-export.

:::note
The word "model" is overloaded in 3D printing — it also means a *printer* model
(Bambu X1C, Prusa MK4) and, internally, an ORM class. When this wiki says "model"
without qualification, it means the library asset.
:::

## Artifact (file)

An **artifact** — also just called a *file* — is one physical stored blob: a
single STL, 3MF, OBJ, or G-code at a particular version under a model. Models are
the concept; artifacts are the bytes on disk (or in S3).

Every artifact carries a version number within its model, so the history of a
model is the ordered list of its artifacts. The actual write path — hash, move
into canonical storage, create the database row, render a thumbnail, extract
metadata — is a single, carefully-ordered sequence that both background ingestion
and revision uploads share. Nothing re-implements it, which is why uploading via
the web UI, the API, and the Orca hook all behave identically.

## Revision

A **revision** is a G-code artifact with a little extra bookkeeping: a label, an
outcome status, free-text notes, and possibly the *recommended* marker. Revision
numbers are derived from version order — they're never stored, so they can't drift
out of sync.

Outcome statuses:

| Status       | Meaning                                            |
| ------------ | -------------------------------------------------- |
| `needs_test` | Sliced but not yet proven on the printer.          |
| `known_good` | Printed successfully; trust this one.              |
| `failed`     | Didn't print right — kept so you don't repeat it.  |
| `archived`   | Old, but retained for history.                     |

### The recommended-revision invariant

This is the rule that makes the model detail page useful: **a model with G-code
always has exactly one recommended revision.** Never zero, never two.

- The *first* G-code you upload to a model automatically claims the marker.
- Marking another revision recommended clears it from all the others.

So when you come back in three months, "which one was the good one?" always has a
single, unambiguous answer staring at you from the top of the page.

## Collections vs. tags

Two different ways to organize, on purpose:

- **Collections** are hierarchical, like folders — `Functional/Brackets`,
  `Minis/Terrain`. A model lives in one place in the tree. Collections support
  subtree moves, model counts, and recursive deletion. They're for *where
  something belongs*.
- **Tags** are a flat vocabulary you apply freely — `petg`, `gift`,
  `needs-supports`. A model can have many. They're for *cross-cutting traits*
  that don't fit a single folder.

Use collections for structure, tags for everything that cuts across structure.

## Trash and the soft-delete lifecycle

Deleting a model doesn't immediately destroy anything. PrintStash uses
soft-delete, and a model moves through a defined lifecycle:

```
soft-delete  →  restore  →  expiry  →  hard delete (rows + blobs)  →  orphan-blob GC
```

A **live** row is one that isn't in the trash; a **trashed** row is soft-deleted
and waiting out its retention window (`VAULT_TRASH_RETENTION_DAYS`, default 30).
Until that window expires you can restore it intact. After it expires, a hard
delete removes the rows and the blobs, and a background pass cleans up any
orphaned blobs. You can also purge the trash manually from **Settings → Trash**.

The practical upshot: a fat-fingered delete is recoverable for a month, and
storage genuinely frees up once things expire.

## Storage keys

Under the hood, every blob is addressed by a **storage key** — an opaque
identifier that's an absolute path on the local backend or an object key on S3.
Callers never branch on which backend is in use; they ask for the key and the
storage layer figures out the rest. That abstraction is why switching from local
disk to S3/R2 is a config change rather than a rewrite.

Thumbnails are stored as WebP for faster library loads. Installs that predate the
WebP switch still have PNG thumbnails — those are read and served until you
rebuild them, but new thumbnails are always WebP.

---

If you want to see how these pieces flow through the system at request time, the
[Architecture](/PrintStash/reference/architecture/) page traces the
router → service → database path and the design decisions behind it.
