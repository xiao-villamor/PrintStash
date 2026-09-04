/**
 * The inbox API client, and the manifest parser that guards it.
 *
 * A pending import's manifest is the only part of the API whose shape PrintStash does
 * *not* control: it is written by a browser extension running on somebody else's machine,
 * against a page somebody else publishes. So the client parses it rather than trusting
 * it, and `parseInboxManifest` returns `null` for anything it cannot vouch for — a
 * missing discriminator, a v2 payload that is not `model_files`, a captured field with a
 * name the backend does not define, or an origin that is neither `confirmed` nor
 * `inferred`.
 *
 * Returning `null` rather than throwing is the design: one malformed capture in a list
 * must not take the whole inbox down, and the caller turns a `null` into a visible
 * "this capture could not be read" rather than a blank screen.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  batchPendingImports,
  capturePendingImport,
  dismissPendingImport,
  getPendingImport,
  importPendingImport,
  listPendingImports,
  parseInboxManifest,
  retryPendingImport,
  updatePendingImport,
} from "@/lib/api/inbox";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

/** The wire shape `parseInboxManifest` accepts, so fixtures need no assertion. */
type ManifestWire = Parameters<typeof parseInboxManifest>[0];

const V2_MANIFEST = {
  schema_version: 2,
  kind: "model_files",
  source: {
    provider: "printables",
    canonical_url: "https://www.printables.com/model/42",
    tags: [],
    fields: { title: { value: "Widget", origin: "confirmed" } },
  },
  files: [{ id: "42:cube", name: "cube.stl", file_type: "stl", size: 1 }],
  selected_ids: ["42:cube"],
};

const ITEM = { id: 1, state: "review", manifest: V2_MANIFEST };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseInboxManifest", () => {
  it("accepts a well-formed v2 capture", () => {
    // SAFETY: `V2_MANIFEST` is written above against the same wire contract the
    // parser reads, so the cast only tells TypeScript what the literal already is.
    expect(parseInboxManifest(V2_MANIFEST as ManifestWire)).not.toBeNull();
  });

  it.each(["direct", "archive", "model_files", "collection", "browser_file"])(
    "accepts a legacy %s capture",
    (kind) => {
      // SAFETY: a bare `kind` is the whole of a legacy manifest's discriminator.
      expect(parseInboxManifest({ kind } as ManifestWire)).not.toBeNull();
    },
  );

  it("refuses a manifest with no kind at all", () => {
    // SAFETY: every field of the wire type is optional, so `{}` is a value the
    // parser really can receive.
    expect(parseInboxManifest({} as ManifestWire)).toBeNull();
  });

  it("refuses a legacy kind it does not know", () => {
    // SAFETY: `kind` is a plain string on the wire; the closed set is the
    // parser's own, which is exactly what this asserts.
    expect(parseInboxManifest({ kind: "surprise" } as ManifestWire)).toBeNull();
  });

  it("refuses a schema version it does not know", () => {
    // SAFETY: `schema_version` is a plain number on the wire.
    expect(parseInboxManifest({ kind: "direct", schema_version: 9 } as ManifestWire)).toBeNull();
  });

  it.each([
    ["a v2 capture that is not model_files", { ...V2_MANIFEST, kind: "direct" }],
    [
      "a source with no provider",
      { ...V2_MANIFEST, source: { ...V2_MANIFEST.source, provider: undefined } },
    ],
    [
      "a source with no canonical url",
      { ...V2_MANIFEST, source: { ...V2_MANIFEST.source, canonical_url: undefined } },
    ],
    ["tags that are not a list", { ...V2_MANIFEST, source: { ...V2_MANIFEST.source, tags: {} } }],
    ["files that are not a list", { ...V2_MANIFEST, files: {} }],
    ["a selection that is not a list", { ...V2_MANIFEST, selected_ids: {} }],
    [
      "a captured field the backend does not define",
      {
        ...V2_MANIFEST,
        source: {
          ...V2_MANIFEST.source,
          fields: { surprise: { value: "x", origin: "confirmed" } },
        },
      },
    ],
    [
      "a captured field with an origin it does not recognise",
      {
        ...V2_MANIFEST,
        source: {
          ...V2_MANIFEST.source,
          fields: { title: { value: "x", origin: "guessed" } },
        },
      },
    ],
  ])("refuses %s", (_name, manifest) => {
    // Written by an extension on somebody else's machine: parsed, never trusted.
    // SAFETY: each case above is a v2 manifest with exactly one field made
    // wrong, which is the shape the parser is built to receive and reject.
    expect(parseInboxManifest(manifest as ManifestWire)).toBeNull();
  });
});

describe("capturePendingImport", () => {
  it("POSTs the captured URL", async () => {
    respondWith(ITEM);

    await capturePendingImport({ url: "https://example.test/model", title: "Widget" });

    expectRequest("/api/v1/inbox", "POST");
    expect(lastBody()).toMatchObject({ url: "https://example.test/model" });
  });
});

describe("listPendingImports", () => {
  it("includes finished captures by default", async () => {
    respondWith([]);

    await listPendingImports();

    expectRequest("/api/v1/inbox?include_completed=true");
  });

  it("can leave the finished ones out", async () => {
    respondWith([]);

    await listPendingImports(false);

    expectRequest("/api/v1/inbox?include_completed=false");
  });

  it("never serves a cached queue", async () => {
    respondWith([]);

    await listPendingImports();

    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("getPendingImport", () => {
  it("returns the parsed capture", async () => {
    respondWith(ITEM);

    const item = await getPendingImport(1);

    expect(item.manifest).not.toBeNull();
    expectRequest("/api/v1/inbox/1");
  });

  it("refuses a capture whose manifest it cannot read", async () => {
    respondWith({ id: 1, state: "review", manifest: { kind: "surprise" } });

    // A silently-dropped manifest would render an empty review screen with no
    // explanation; the caller needs to know the parse failed.
    await expect(getPendingImport(1)).rejects.toThrow("Invalid inbox manifest response");
  });
});

describe("updatePendingImport", () => {
  it("PATCHes only what changed", async () => {
    respondWith(ITEM);

    await updatePendingImport(1, { title: "Renamed" });

    expectRequest("/api/v1/inbox/1", "PATCH");
    expect(lastBody()).toEqual({ title: "Renamed" });
  });
});

describe("importPendingImport", () => {
  it("POSTs the chosen files", async () => {
    respondWith(ITEM);

    await importPendingImport(1, ["42:cube"]);

    expectRequest("/api/v1/inbox/1/import", "POST");
    expect(lastBody()).toEqual({ selected_ids: ["42:cube"] });
  });
});

describe("retryPendingImport", () => {
  it("POSTs to the retry sub-resource", async () => {
    respondWith(ITEM);

    await retryPendingImport(1);

    expectRequest("/api/v1/inbox/1/retry", "POST");
  });
});

describe("dismissPendingImport", () => {
  it("DELETEs the capture", async () => {
    respondWith(null, 204);

    await dismissPendingImport(1);

    expectRequest("/api/v1/inbox/1", "DELETE");
  });
});

describe("batchPendingImports", () => {
  it("POSTs one action for many captures", async () => {
    respondWith([ITEM]);

    await batchPendingImports({ item_ids: [1, 2], action: "dismiss" });

    expectRequest("/api/v1/inbox/batch", "POST");
    expect(lastBody()).toEqual({ item_ids: [1, 2], action: "dismiss" });
  });
});
