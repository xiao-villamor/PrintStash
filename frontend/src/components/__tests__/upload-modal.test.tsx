import "@testing-library/jest-dom/vitest";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BulkFiles } from "@/components/upload-modal";
import type { BulkItem } from "@/lib/bulk-upload";

function makeFile(name: string): File {
  return new File(["data"], name);
}

function bulkItem(name: string, relPath = ""): BulkItem {
  return { file: makeFile(name), relPath };
}

/**
 * The slice of a dropped FileSystemFileEntry the walk in bulk-upload actually
 * reads: the File System API discriminators plus `file()`. Naming the slice
 * keeps the fixture honest instead of dressing a partial object up as the full
 * lib.dom interface.
 */
interface DroppedFileEntry {
  isFile: true;
  isDirectory: false;
  fullPath: string;
  name: string;
  file: (resolve: (f: File) => void) => void;
}

function fileEntry(fullPath: string): DroppedFileEntry {
  const name = fullPath.split("/").pop() ?? "";
  return {
    isFile: true,
    isDirectory: false,
    fullPath,
    name,
    file: (resolve: (f: File) => void) => resolve(makeFile(name)),
  };
}

type BulkFilesProps = Parameters<typeof BulkFiles>[0];

function renderBulk(over: Partial<BulkFilesProps> = {}) {
  const onAddItems = vi.fn<BulkFilesProps["onAddItems"]>();
  const onRemove = vi.fn<BulkFilesProps["onRemove"]>();
  const onClear = vi.fn<BulkFilesProps["onClear"]>();
  const props: BulkFilesProps = {
    items: [],
    fileInputRef: createRef<HTMLInputElement>(),
    folderInputRef: createRef<HTMLInputElement>(),
    onAddItems,
    onRemove,
    onClear,
    ...over,
  };
  const utils = render(<BulkFiles {...props} />);
  return { ...utils, onAddItems, onRemove, onClear };
}

/** The drop target wrapping the hint text; every drop test aims at it. */
function dropZone(): HTMLElement {
  const zone = screen.getByText(/drop 3d models or a folder here/i).closest("div");
  if (zone === null) throw new Error("drop zone not rendered");
  return zone;
}

describe("BulkFiles", () => {
  it("shows the drop-zone hint and a folder-select action when empty", () => {
    renderBulk();
    expect(screen.getByText(/drop 3d models or a folder here/i)).toBeInTheDocument();
    expect(screen.getByText(/subfolders become nested collections/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /select a folder/i })).toBeInTheDocument();
  });

  it("queues files picked through the file input", async () => {
    const { container, onAddItems } = renderBulk();
    const fileInput = container.querySelectorAll<HTMLInputElement>('input[type="file"]')[0];
    await userEvent.upload(fileInput, [makeFile("foo.stl")]);

    expect(onAddItems).toHaveBeenCalledTimes(1);
    const passed = onAddItems.mock.calls[0][0];
    expect(passed).toHaveLength(1);
    expect(passed[0].file.name).toBe("foo.stl");
    expect(passed[0].relPath).toBe("");
  });

  it("queues a folder picked through the folder input", async () => {
    const { container, onAddItems } = renderBulk();
    const folderInput = container.querySelectorAll<HTMLInputElement>('input[type="file"]')[1];
    expect(folderInput).toHaveAttribute("webkitdirectory");

    await userEvent.upload(folderInput, [makeFile("a.stl")]);
    expect(onAddItems).toHaveBeenCalledTimes(1);
  });

  it("recurses a dropped entry tree into the queue", async () => {
    const { onAddItems } = renderBulk();

    fireEvent.drop(dropZone(), {
      dataTransfer: {
        items: [{ webkitGetAsEntry: () => fileEntry("/Lib/foo.stl") }],
        files: [],
      },
    });

    await waitFor(() => expect(onAddItems).toHaveBeenCalledTimes(1));
    const passed = onAddItems.mock.calls[0][0];
    expect(passed[0].file.name).toBe("foo.stl");
    expect(passed[0].relPath).toBe("Lib");
  });

  it("falls back to a flat FileList when the entries API is unavailable", async () => {
    const { onAddItems } = renderBulk();

    fireEvent.drop(dropZone(), {
      dataTransfer: { items: [], files: [makeFile("flat.stl")] },
    });

    await waitFor(() => expect(onAddItems).toHaveBeenCalledTimes(1));
    const passed = onAddItems.mock.calls[0][0];
    expect(passed[0].file.name).toBe("flat.stl");
    expect(passed[0].relPath).toBe("");
  });

  it("renders queued items with their folder prefix and a folder summary", () => {
    renderBulk({
      items: [
        bulkItem("top.stl", "Lib"),
        bulkItem("small.stl", "Lib/brackets"),
        bulkItem("loose.stl", ""),
      ],
    });
    expect(screen.getByText("top.stl")).toBeInTheDocument();
    expect(screen.getByText("Lib/brackets/")).toBeInTheDocument();
    // 3 files spanning 2 distinct folders.
    expect(screen.getByText(/3 files · 2 folders/i)).toBeInTheDocument();
  });

  it("invokes onRemove and onClear from the list controls", async () => {
    const { onRemove, onClear } = renderBulk({ items: [bulkItem("a.stl", "Lib")] });
    await userEvent.click(screen.getByRole("button", { name: /remove a.stl/i }));
    expect(onRemove).toHaveBeenCalledWith(0);

    await userEvent.click(screen.getByRole("button", { name: /^clear$/i }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
