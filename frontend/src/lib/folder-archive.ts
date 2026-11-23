/** Bounded, lossless folder transfer into the server's existing archive review. */
import { Zip, ZipPassThrough } from "fflate";
import type { BulkItem } from "./bulk-upload";

export const FOLDER_ARCHIVE_MAX_BYTES = 256 * 1024 * 1024;
export const FOLDER_ARCHIVE_MAX_FILES = 2000;

export function folderEntryName(item: BulkItem): string {
  const path = [item.relPath, item.file.name].filter(Boolean).join("/");
  if (
    path.includes("\\") ||
    path.startsWith("/") ||
    path.includes("\0") ||
    path.split("/").some((part) => !part || part === "." || part === "..")
  ) {
    throw new Error("folder_path_invalid");
  }
  return path;
}

/** Preserve auxiliaries for review; conflicting paths require an explicit choice. */
export function mergeFolderItems(existing: BulkItem[], incoming: BulkItem[]): BulkItem[] {
  const byName = new Map(existing.map((item) => [folderEntryName(item), item]));
  for (const item of incoming) {
    const path = folderEntryName(item);
    const previous = byName.get(path);
    if (previous && previous.file !== item.file) throw new Error("folder_path_conflict");
    byName.set(path, item);
  }
  return [...byName.values()];
}

export async function prepareFolderArchive(
  items: readonly BulkItem[],
  signal: AbortSignal,
): Promise<File> {
  if (items.length === 0 || items.length > FOLDER_ARCHIVE_MAX_FILES)
    throw new Error("folder_entry_limit");
  if (items.reduce((sum, item) => sum + item.file.size, 0) > FOLDER_ARCHIVE_MAX_BYTES)
    throw new Error("folder_size_limit");
  const roots = new Set(items.map((item) => item.relPath.split("/")[0]));
  const root = roots.size === 1 ? [...roots][0] : "";
  const names = items.map((item) => {
    const path = folderEntryName(item);
    return root ? path.slice(root.length + 1) : path;
  });
  if (new Set(names).size !== names.length) throw new Error("folder_path_conflict");
  signal.throwIfAborted();
  const chunks: BlobPart[] = [];
  let failure: Error | null = null;
  const archive = new Zip((error, data) => {
    if (error) failure = error;
    else chunks.push(new Uint8Array(data).buffer);
  });
  try {
    for (const [index, item] of items.entries()) {
      signal.throwIfAborted();
      const entry = new ZipPassThrough(names[index]);
      archive.add(entry);
      for (let offset = 0; offset < item.file.size; offset += 1024 * 1024) {
        const bytes = new Uint8Array(
          await item.file.slice(offset, offset + 1024 * 1024).arrayBuffer(),
        );
        signal.throwIfAborted();
        entry.push(bytes, false);
        if (failure) throw failure;
      }
      entry.push(new Uint8Array(), true);
    }
    archive.end();
    if (failure) throw failure;
    return new File(chunks, `${root || "folder"}.zip`, { type: "application/zip" });
  } catch (error) {
    archive.terminate();
    throw error;
  }
}
