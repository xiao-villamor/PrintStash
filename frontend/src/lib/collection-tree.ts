/**
 * Folder trees built from what a page already holds, not from the whole library.
 *
 * The sidebar used to load every collection and derive everything from that list
 * — which is what took a 9,000-collection library a minute to open (#295). It now
 * loads one level at a time; while the outliner is filtered it shows only the
 * folders on the way to what matched. Those folders come from paths and labels
 * the server sends with each item (`collection` and `collection_label`), so the
 * tree needs no list of its own.
 */

import type { CollectionNodeRead } from "@/types";

/** One folder on the way to something that matched. */
export interface FilteredFolder {
  path: string;
  name: string;
  /** The collection itself when the server returned it; ancestors may have none. */
  node: CollectionNodeRead | null;
  children: FilteredFolder[];
}

/** Something that sits in a folder: a matched collection, or a leaf's folder. */
export interface FolderEntry {
  path: string;
  /** Names from the first visible ancestor down, e.g. `Parts/Brackets`. */
  label: string;
  node?: CollectionNodeRead;
}

/** The paths that must be open for `path` to be visible, root first, excluding `path`. */
export function ancestorPaths(path: string): string[] {
  const parts = path.split("/");
  return parts.slice(1).map((_, index) => parts.slice(0, index + 1).join("/"));
}

/**
 * Pair each named segment of a label with the path it names, root first.
 *
 * A label only names the ancestors the reader can see, so it may be shorter than
 * the path: it names the *last* folders of the path. Paths are slugs and never
 * shown; this is how a folder gets its real name without being loaded.
 */
export function labelledChain(path: string, label: string): { path: string; name: string }[] {
  const prefixes = [...ancestorPaths(path), path];
  const names = label.split("/");
  const named = prefixes.slice(prefixes.length - names.length);
  return named.map((prefix, index) => ({ path: prefix, name: names[index] }));
}

/**
 * The smallest tree that reaches every entry, siblings sorted by name.
 *
 * Folders above what the reader can see are left out, as the tree leaves them
 * out; an entry's first visible folder becomes a root.
 */
export function buildFilteredTree(entries: FolderEntry[]): FilteredFolder[] {
  const byPath = new Map<string, FilteredFolder>();
  const roots: FilteredFolder[] = [];
  for (const entry of entries) {
    let parent: FilteredFolder | null = null;
    for (const link of labelledChain(entry.path, entry.label)) {
      let folder = byPath.get(link.path);
      if (folder === undefined) {
        folder = { path: link.path, name: link.name, node: null, children: [] };
        byPath.set(link.path, folder);
        (parent === null ? roots : parent.children).push(folder);
      }
      parent = folder;
    }
    if (parent !== null && entry.node !== undefined) parent.node = entry.node;
  }
  const sort = (folders: FilteredFolder[]) => {
    folders.sort((a, b) => a.name.localeCompare(b.name));
    for (const folder of folders) sort(folder.children);
  };
  sort(roots);
  return roots;
}
