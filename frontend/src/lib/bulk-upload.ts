// Pure logic + filesystem-entry walking for the "Bulk" upload tab. Kept out of
// the React component so each step (path parsing, mesh filtering, dedup,
// folder→collection mapping, and dropped-folder recursion) is unit-testable.

const MESH_EXTENSIONS = new Set([".stl", ".3mf", ".obj", ".step", ".stp"]);
export const MESH_ACCEPT = ".stl,.3mf,.obj,.step,.stp";
const GCODE_EXTENSIONS = new Set([".gcode", ".g", ".gco", ".bgcode"]);
export const GCODE_ACCEPT = ".gcode,.g,.gco,.bgcode";

// A queued mesh tagged with the folder path it came from (relative to the
// drop/pick root, no filename) so we can mirror folders into nested
// collections. `relPath` is "" for individually-picked files.
export interface BulkItem {
  file: File;
  relPath: string;
}

export interface BulkMergeResult {
  items: BulkItem[];
  added: number;
  skipped: number;
}

/** Lower-cased extension including the dot, e.g. "foo.STL" → ".stl". */
export function extensionOf(filename: string): string {
  return "." + (filename.split(".").pop()?.toLowerCase() ?? "");
}

export function isMeshFile(name: string): boolean {
  return MESH_EXTENSIONS.has(extensionOf(name));
}

export function isGcodeFile(name: string): boolean {
  return GCODE_EXTENSIONS.has(extensionOf(name));
}

/** Folder portion of a path, no filename. "Lib/brackets/foo.stl" → "Lib/brackets". */
export function dirOf(path: string): string {
  const i = path.lastIndexOf("/");
  return i === -1 ? "" : path.slice(0, i);
}

/** Nested collection for a file: base collection joined with its source folder. */
export function bulkTargetCollection(base: string, relPath: string): string {
  return [base, relPath].filter(Boolean).join("/");
}

// A multi-file / <input webkitdirectory> FileList carries each file's folder
// path in `webkitRelativePath`; turn it into BulkItems.
export function fileListToItems(files: FileList | File[]): BulkItem[] {
  return Array.from(files).map((file) => ({
    file,
    relPath: dirOf(file.webkitRelativePath || ""),
  }));
}

// Merge newly picked/dropped items into an existing queue: drop non-mesh files
// and duplicates (matched by folder path + name + size). Returns counts so the
// caller can surface a "some files skipped" notice.
export function mergeBulkItems(existing: BulkItem[], incoming: BulkItem[]): BulkMergeResult {
  const meshes = incoming.filter((it) => isMeshFile(it.file.name));
  const skipped = incoming.length - meshes.length;
  const keyOf = (it: BulkItem) => `${it.relPath}/${it.file.name}:${it.file.size}`;
  const seen = new Set(existing.map(keyOf));
  const items = [...existing];
  let added = 0;
  for (const it of meshes) {
    const key = keyOf(it);
    if (!seen.has(key)) {
      seen.add(key);
      items.push(it);
      added += 1;
    }
  }
  return { items, added, skipped };
}

// Only the entry handle is read off each dropped item, and older engines ship
// items without `webkitGetAsEntry` at all — so the seam names that slice rather
// than demanding a whole DataTransferItemList.
export interface DroppedItem {
  webkitGetAsEntry?: () => FileSystemEntry | null;
}

// Synchronously pull FileSystemEntry handles out of a drop. Must run inside the
// drop handler — the DataTransfer (and its items) is emptied once that returns;
// the entries themselves stay valid for the async walk that follows.
export function entriesFromDataTransfer(
  items: ArrayLike<DroppedItem> | null | undefined,
): FileSystemEntry[] {
  if (!items) return [];
  const out: FileSystemEntry[] = [];
  for (const item of Array.from(items)) {
    const entry = item.webkitGetAsEntry?.();
    if (entry) out.push(entry);
  }
  return out;
}

function readEntryFile(entry: FileSystemFileEntry): Promise<File> {
  return new Promise((resolve, reject) => entry.file(resolve, reject));
}

// A directory reader returns children in batches; keep calling until drained.
function readAllDirEntries(reader: FileSystemDirectoryReader): Promise<FileSystemEntry[]> {
  return new Promise((resolve, reject) => {
    const out: FileSystemEntry[] = [];
    const pump = () =>
      reader.readEntries((batch) => {
        if (batch.length === 0) resolve(out);
        else {
          out.push(...batch);
          pump();
        }
      }, reject);
    pump();
  });
}

// `isFile` / `isDirectory` are the File System API's own discriminators for a
// FileSystemEntry; lib.dom declares them as plain booleans, so restate them as
// type predicates and the two subtypes narrow without a cast.
function isFileEntry(entry: FileSystemEntry): entry is FileSystemFileEntry {
  return entry.isFile;
}

function isDirectoryEntry(entry: FileSystemEntry): entry is FileSystemDirectoryEntry {
  return entry.isDirectory;
}

// Recursively walk a dropped file/dir entry into BulkItems, preserving the
// folder path from `fullPath` (which looks like "/Lib/brackets/foo.stl").
export async function walkEntry(entry: FileSystemEntry): Promise<BulkItem[]> {
  if (isFileEntry(entry)) {
    const file = await readEntryFile(entry);
    return [{ file, relPath: dirOf(entry.fullPath.replace(/^\/+/, "")) }];
  }
  if (isDirectoryEntry(entry)) {
    const children = await readAllDirEntries(entry.createReader());
    const nested = await Promise.all(children.map(walkEntry));
    return nested.flat();
  }
  return [];
}

/** Walk a set of dropped entries (files and/or folders) into a flat queue. */
export async function walkEntries(entries: FileSystemEntry[]): Promise<BulkItem[]> {
  const nested = await Promise.all(entries.map(walkEntry));
  return nested.flat();
}
