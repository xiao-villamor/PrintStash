/*
 * The pure logic behind dropping a folder of models onto the app.
 *
 * A bulk upload is a path problem. `dirOf` and `bulkTargetCollection` decide which
 * collection each file lands in, and getting them wrong scatters somebody's
 * organised folder tree across the vault — recoverable only by hand, model by
 * model. So the boundaries are all here: a bare filename, an empty base, both
 * empty (the vault root), and the nested case.
 *
 * `mergeBulkItems` is the other half, and its identity is `folder + name + size`
 * rather than name alone. Two files called `part.stl` in different folders are
 * two different models; the same file dropped twice is one. Both directions are
 * asserted, and so is deduplication *within* one batch — a folder drop can contain
 * the same file twice.
 *
 * `walkEntries` drains the directory reader in batches, because that API returns
 * children a page at a time and stopping at the first page silently imports part
 * of a folder.
 */

import { describe, expect, it } from "vitest";

import {
  BULK_UPLOAD_CONCURRENCY,
  bulkTargetCollection,
  dirOf,
  entriesFromDataTransfer,
  extensionOf,
  fileListToItems,
  GCODE_ACCEPT,
  isGcodeFile,
  isMeshFile,
  mergeBulkItems,
  runBounded,
  walkEntries,
  type BulkItem,
} from "@/lib/bulk-upload";

// --- helpers -------------------------------------------------------------

function makeFile(name: string, relPath = "", size = 4): File {
  const f = new File(["x".repeat(size)], name);
  if (relPath) {
    Object.defineProperty(f, "webkitRelativePath", { value: relPath });
  }
  return f;
}

function item(name: string, relPath = "", size = 4): BulkItem {
  return { file: makeFile(name, "", size), relPath };
}

function fileEntry(fullPath: string): FileSystemFileEntry {
  const name = fullPath.split("/").pop() ?? "";
  // SAFETY: walkEntry reads only isFile/isDirectory/fullPath/name and invokes
  // file(resolve); it never touches `filesystem` or `getParent`, the two
  // members of the DOM interface this literal omits.
  return {
    isFile: true,
    isDirectory: false,
    fullPath,
    name,
    file: (resolve: (f: File) => void) => resolve(makeFile(name)),
  } as FileSystemFileEntry;
}

// Directory whose reader yields `batches` in sequence, then an empty batch —
// exercising the "keep reading until drained" loop.
function dirEntry(fullPath: string, batches: FileSystemEntry[][]): FileSystemDirectoryEntry {
  let i = 0;
  // SAFETY: walkEntry reads only isFile/isDirectory/fullPath/name and calls
  // createReader().readEntries(resolve); `filesystem`, `getParent`, `getFile`
  // and `getDirectory` are never reached, so omitting them is safe.
  return {
    isFile: false,
    isDirectory: true,
    fullPath,
    name: fullPath.split("/").pop() ?? "",
    createReader: () => ({
      readEntries: (resolve: (e: FileSystemEntry[]) => void) => {
        resolve(i < batches.length ? batches[i++] : []);
      },
    }),
  } as FileSystemDirectoryEntry;
}

// --- extensionOf ---------------------------------------------------------

describe("extensionOf", () => {
  it("lower-cases and keeps the dot", () => {
    expect(extensionOf("Part.STL")).toBe(".stl");
  });
  it("handles multiple dots", () => {
    expect(extensionOf("bracket.v2.3mf")).toBe(".3mf");
  });
  it("returns a dot-prefixed whole name when there is no extension", () => {
    // No "." → split returns the whole string as the last segment.
    expect(extensionOf("README")).toBe(".readme");
  });
});

// --- isMeshFile ----------------------------------------------------------

describe("isMeshFile", () => {
  it.each([
    ["foo.stl", true],
    ["foo.3mf", true],
    ["foo.obj", true],
    ["foo.step", true],
    ["foo.stp", true],
    ["foo.dxf", true],
    ["FOO.STL", true],
    ["foo.gcode", false],
    ["foo.png", false],
    ["foo", false],
  ])("%s → %s", (name, expected) => {
    expect(isMeshFile(name)).toBe(expected);
  });
});

// --- isGcodeFile ---------------------------------------------------------

describe("isGcodeFile", () => {
  it.each([
    ["foo.gcode", true],
    ["foo.g", true],
    ["foo.gco", true],
    ["foo.bgcode", true],
    ["FOO.BGCODE", true],
    ["foo.stl", false],
    ["foo", false],
  ])("classifies %s as %s", (name, expected) => {
    expect(isGcodeFile(name)).toBe(expected);
  });

  it("advertises every accepted extension to native file pickers", () => {
    expect(GCODE_ACCEPT).toBe(".gcode,.g,.gco,.bgcode");
  });
});

// --- dirOf ---------------------------------------------------------------

describe("dirOf", () => {
  it("returns nested folder path", () => {
    expect(dirOf("Lib/brackets/foo.stl")).toBe("Lib/brackets");
  });
  it("returns single folder", () => {
    expect(dirOf("Lib/foo.stl")).toBe("Lib");
  });
  it("returns empty for a bare filename", () => {
    expect(dirOf("foo.stl")).toBe("");
  });
  it("returns empty for empty input", () => {
    expect(dirOf("")).toBe("");
  });
});

// --- bulkTargetCollection ------------------------------------------------

describe("bulkTargetCollection", () => {
  it("joins base and relative folder", () => {
    expect(bulkTargetCollection("Imports", "Lib/brackets")).toBe("Imports/Lib/brackets");
  });
  it("uses just the relative path when base is empty", () => {
    expect(bulkTargetCollection("", "Lib/brackets")).toBe("Lib/brackets");
  });
  it("uses just the base when there is no relative path", () => {
    expect(bulkTargetCollection("Imports", "")).toBe("Imports");
  });
  it("is empty when both are empty (vault, no collection)", () => {
    expect(bulkTargetCollection("", "")).toBe("");
  });
});

// --- fileListToItems -----------------------------------------------------

describe("fileListToItems", () => {
  it("gives a flat relPath for individually-picked files", () => {
    const items = fileListToItems([makeFile("foo.stl")]);
    expect(items).toEqual([{ file: expect.any(File), relPath: "" }]);
  });
  it("derives relPath from webkitRelativePath for folder picks", () => {
    const items = fileListToItems([
      makeFile("foo.stl", "MyLib/brackets/foo.stl"),
      makeFile("bar.stl", "MyLib/bar.stl"),
    ]);
    expect(items.map((i) => i.relPath)).toEqual(["MyLib/brackets", "MyLib"]);
  });
});

// --- mergeBulkItems ------------------------------------------------------

describe("mergeBulkItems", () => {
  it("filters out non-mesh files and reports the skip count", () => {
    const res = mergeBulkItems([], [item("a.stl"), item("notes.txt"), item("pic.png")]);
    expect(res.items.map((i) => i.file.name)).toEqual(["a.stl"]);
    expect(res.added).toBe(1);
    expect(res.skipped).toBe(2);
  });

  it("dedups by folder + name + size against the existing queue", () => {
    const existing = [item("a.stl", "Lib", 10)];
    const res = mergeBulkItems(existing, [
      item("a.stl", "Lib", 10), // exact dup → skipped
      item("a.stl", "Lib", 99), // same name, different size → kept
    ]);
    expect(res.items).toHaveLength(2);
    expect(res.added).toBe(1);
  });

  it("keeps same-named files from different folders", () => {
    const res = mergeBulkItems([], [item("foo.stl", "a"), item("foo.stl", "b")]);
    expect(res.items).toHaveLength(2);
    expect(res.items.map((i) => i.relPath)).toEqual(["a", "b"]);
  });

  it("dedups within a single incoming batch too", () => {
    const res = mergeBulkItems([], [item("x.stl"), item("x.stl")]);
    expect(res.items).toHaveLength(1);
    expect(res.added).toBe(1);
  });
});

// --- entriesFromDataTransfer --------------------------------------------

describe("entriesFromDataTransfer", () => {
  it("returns [] for a null item list", () => {
    expect(entriesFromDataTransfer(null)).toEqual([]);
  });

  it("collects non-null entries and skips items that yield null", () => {
    const a = fileEntry("/a.stl");
    const fakeList = [
      { webkitGetAsEntry: () => a },
      { webkitGetAsEntry: () => null },
      {}, // no webkitGetAsEntry at all
    ];
    expect(entriesFromDataTransfer(fakeList)).toEqual([a]);
  });
});

// --- walkEntries (recursion + batched reads) -----------------------------

describe("walkEntries", () => {
  it("maps a dropped file to a flat item", async () => {
    const items = await walkEntries([fileEntry("/foo.stl")]);
    expect(items).toEqual([{ file: expect.any(File), relPath: "" }]);
  });

  it("recurses into nested folders, preserving structure", async () => {
    const tree = dirEntry("/Lib", [
      [
        fileEntry("/Lib/top.stl"),
        dirEntry("/Lib/brackets", [[fileEntry("/Lib/brackets/small.stl")]]),
      ],
    ]);
    const items = await walkEntries([tree]);
    const byName = Object.fromEntries(items.map((i) => [i.file.name, i.relPath]));
    expect(byName).toEqual({
      "top.stl": "Lib",
      "small.stl": "Lib/brackets",
    });
  });

  it("drains a directory reader that returns children in batches", async () => {
    const dir = dirEntry("/Lib", [[fileEntry("/Lib/a.stl")], [fileEntry("/Lib/b.stl")]]);
    const items = await walkEntries([dir]);
    expect(items.map((i) => i.file.name).sort()).toEqual(["a.stl", "b.stl"]);
  });

  it("ignores a dropped entry whose kind is unsupported", async () => {
    // SAFETY: walkEntry reads only the two kind flags before returning an empty
    // result, so the remaining FileSystemEntry members are deliberately absent.
    const unsupported = {
      isFile: false,
      isDirectory: false,
      fullPath: "/device",
      name: "device",
    } as FileSystemEntry;

    expect(await walkEntries([unsupported])).toEqual([]);
  });
});

// --- runBounded ------------------------------------------------------------

describe("runBounded", () => {
  /** Work that records how many items were in flight when each one started. */
  function tracked() {
    let active = 0;
    const started: string[] = [];
    const peaks: number[] = [];
    const release = new Map<string, () => void>();
    const work = (name: string): Promise<void> => {
      active += 1;
      started.push(name);
      peaks.push(active);
      return new Promise<void>((resolve) => {
        release.set(name, () => {
          active -= 1;
          resolve();
        });
      });
    };
    return { started, peaks, release, work };
  }

  const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

  it("keeps no more than the limit in flight", async () => {
    const { started, peaks, release, work } = tracked();
    const done = runBounded(["a", "b", "c", "d", "e"], 2, work);
    await settle();
    expect(started).toEqual(["a", "b"]);

    for (const name of ["a", "b", "c", "d", "e"]) {
      release.get(name)?.();
      await settle();
    }
    await done;

    expect(Math.max(...peaks)).toBe(2);
  });

  it("starts files in queue order", async () => {
    const { started, release, work } = tracked();
    const done = runBounded(["a", "b", "c", "d"], 2, work);
    await settle();
    release.get("b")?.();
    await settle();
    release.get("a")?.();
    await settle();
    release.get("c")?.();
    release.get("d")?.();
    await done;

    expect(started).toEqual(["a", "b", "c", "d"]);
  });

  it("runs every file exactly once", async () => {
    const seen: number[] = [];

    await runBounded([1, 2, 3, 4, 5, 6, 7], 3, async (value) => void seen.push(value));

    expect(seen.toSorted((a, b) => a - b)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("finishes at once for an empty queue", async () => {
    const seen: number[] = [];

    await runBounded<number>([], 3, async (value) => void seen.push(value));

    expect(seen).toEqual([]);
  });

  it("uses one lane per file when the queue is shorter than the limit", async () => {
    const { peaks, release, work } = tracked();
    const done = runBounded(["a", "b"], 5, work);
    await settle();
    release.get("a")?.();
    release.get("b")?.();
    await done;

    expect(Math.max(...peaks)).toBe(2);
  });

  it("keeps the other lanes going when one file's work rejects", async () => {
    const seen: string[] = [];

    await runBounded(["bad", "b", "c"], 2, async (name) => {
      if (name === "bad") throw new Error("lane failed");
      seen.push(name);
    });

    expect(seen).toEqual(["b", "c"]);
  });

  it.each([0, -1, 1.5])("refuses a limit of %s", async (limit) => {
    await expect(runBounded(["a"], limit, async () => {})).rejects.toThrow("bulk_upload_limit");
  });

  it("stays below the server's four staged uploads per user", () => {
    expect(BULK_UPLOAD_CONCURRENCY).toBe(3);
  });
});
