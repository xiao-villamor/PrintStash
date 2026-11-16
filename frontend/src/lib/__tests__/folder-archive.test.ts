/** Folder review preserves original members while bounding client-side staging. */
import { describe, expect, it } from "vitest";
import { unzipSync, strFromU8 } from "fflate";
import {
  folderEntryName,
  mergeFolderItems,
  prepareFolderArchive,
  FOLDER_ARCHIVE_MAX_BYTES,
  FOLDER_ARCHIVE_MAX_FILES,
} from "../folder-archive";
import type { BulkItem } from "../bulk-upload";

// jsdom lacks Blob.arrayBuffer/text; FileReader is its real byte reader.
class FolderFile extends File {
  override slice(start?: number, end?: number, contentType?: string): Blob {
    const blob = super.slice(start, end, contentType);
    Object.defineProperty(blob, "arrayBuffer", { value: async () => (await bytes(blob)).buffer });
    return blob;
  }
  override async text(): Promise<string> {
    return new TextDecoder().decode(await bytes(this));
  }
}

function item(name: string, body: string, relPath = "Assembly"): BulkItem {
  return { file: new FolderFile([body], name), relPath };
}

async function bytes(file: Blob): Promise<Uint8Array<ArrayBuffer>> {
  return new Uint8Array(
    await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        if (reader.result instanceof ArrayBuffer) resolve(reader.result);
        else reject(new Error("not_binary"));
      };
      reader.onerror = () => reject(reader.error);
      reader.readAsArrayBuffer(file);
    }),
  );
}

describe("prepareFolderArchive", () => {
  it.each([0, FOLDER_ARCHIVE_MAX_FILES + 1])(
    "refuses an invalid entry count: %s",
    async (count) => {
      const source = item("part.stl", "mesh");
      await expect(
        prepareFolderArchive(
          Array.from({ length: count }, () => source),
          new AbortController().signal,
        ),
      ).rejects.toThrow("folder_entry_limit");
    },
  );
  it("rejects an interrupted file read", async () => {
    const source = item("part.stl", "original");
    Object.defineProperty(source.file, "slice", {
      value: () => {
        const blob = new Blob();
        Object.defineProperty(blob, "arrayBuffer", {
          value: async () => {
            throw new Error("read_failed");
          },
        });
        return blob;
      },
    });
    await expect(prepareFolderArchive([source], new AbortController().signal)).rejects.toThrow(
      "read_failed",
    );
    expect(await source.file.text()).toBe("original");
  });
  it("stops cancellation during a file read", async () => {
    const controller = new AbortController();
    const source = item("part.stl", "original");
    Object.defineProperty(source.file, "slice", {
      value: () => {
        const blob = new Blob();
        Object.defineProperty(blob, "arrayBuffer", {
          value: async () => {
            controller.abort(new Error("cancelled_during_read"));
            return new ArrayBuffer(0);
          },
        });
        return blob;
      },
    });
    await expect(prepareFolderArchive([source], controller.signal)).rejects.toThrow(
      "cancelled_during_read",
    );
    expect(await source.file.text()).toBe("original");
  });
  it("preserves distinct folder roots", async () => {
    const zip = await prepareFolderArchive(
      [item("part.stl", "first", "A"), item("part.stl", "second", "B")],
      new AbortController().signal,
    );
    const entries = unzipSync(await bytes(zip));
    expect(zip.name).toBe("folder.zip");
    expect(Object.keys(entries)).toEqual(["A/part.stl", "B/part.stl"]);
    expect(strFromU8(entries["A/part.stl"])).toBe("first");
    expect(strFromU8(entries["B/part.stl"])).toBe("second");
  });
  it("preserves the complete folder for review", async () => {
    const items = [
      item("part.stl", "mesh"),
      item("guide.pdf", "pdf"),
      item("photo.jpg", "image"),
      item("README.md", "instructions"),
      item("other.bin", "unknown"),
    ];
    const zip = await prepareFolderArchive(items, new AbortController().signal);
    const entries = unzipSync(await bytes(zip));
    expect(Object.keys(entries)).toEqual(items.map((item) => item.file.name));
    for (const source of items)
      expect(strFromU8(entries[source.file.name])).toBe(await source.file.text());
  });
  it("refuses a folder beyond the byte budget before reading", async () => {
    const source = item("large.stl", "small");
    Object.defineProperty(source.file, "size", { value: FOLDER_ARCHIVE_MAX_BYTES + 1 });
    await expect(prepareFolderArchive([source], new AbortController().signal)).rejects.toThrow(
      "folder_size_limit",
    );
  });
  it("stops a cancelled preparation", async () => {
    const controller = new AbortController();
    controller.abort(new Error("cancelled"));
    await expect(
      prepareFolderArchive([item("part.stl", "mesh")], controller.signal),
    ).rejects.toThrow("cancelled");
  });
  it("refuses conflicting archive paths", async () => {
    await expect(
      prepareFolderArchive(
        [item("part.stl", "first"), item("part.stl", "second")],
        new AbortController().signal,
      ),
    ).rejects.toThrow("folder_path_conflict");
  });
});

describe("folderEntryName", () => {
  it.each(["../escape", "/absolute", "a/../b", "a\\b", "a//b"])(
    "refuses unsafe folder %s",
    (path) => {
      expect(() => folderEntryName(item("part.stl", "mesh", path))).toThrow("folder_path_invalid");
    },
  );
});

describe("mergeFolderItems", () => {
  it("retains auxiliary files", () => {
    const source = item("manual.pdf", "pdf");
    expect(mergeFolderItems([], [source])).toEqual([source]);
  });
  it("refuses to replace an already selected file", () => {
    expect(() => mergeFolderItems([item("part.stl", "old")], [item("part.stl", "new")])).toThrow(
      "folder_path_conflict",
    );
  });
});
