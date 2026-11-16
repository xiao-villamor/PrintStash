/** Collection navigation follows an authorized identity, never a guessed path prefix. */
import { useEffect, useState } from "react";
import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { collectionLookupOptions, collectionLookupByIdOptions, useQueryApi } from "@/lib/queries";
import type { CollectionLookupRead } from "@/types";

const IDENTIFIED_KEY = "ps-recent-folders-identified";
const LABELLED_KEY = "ps-recent-folders-labelled";
const LEGACY_KEY = "ps-recent-folders";
const LIMIT = 6;
interface RecentFolder {
  id: number | null;
  path: string;
  label: string;
}
type Stored =
  | string
  | number
  | boolean
  | null
  | readonly Stored[]
  | { readonly [key: string]: Stored };

function storedArray(key: string): readonly Stored[] {
  try {
    const raw = localStorage.getItem(key);
    if (raw === null) return [];
    const value: Stored = JSON.parse(raw);
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

// oxlint-disable anti-slop/no-runtime-typeof -- Persisted browser preferences are foreign input; accept only the documented tuple shape.
function readRecentFolders(): RecentFolder[] {
  if (localStorage.getItem(IDENTIFIED_KEY) !== null)
    return storedArray(IDENTIFIED_KEY)
      .flatMap((entry): RecentFolder[] =>
        Array.isArray(entry) &&
        entry.length === 3 &&
        (entry[0] === null ||
          (typeof entry[0] === "number" && Number.isSafeInteger(entry[0]) && entry[0] > 0)) &&
        typeof entry[1] === "string" &&
        entry[1].length > 0 &&
        typeof entry[2] === "string"
          ? [{ id: entry[0], path: entry[1], label: entry[2] }]
          : [],
      )
      .slice(0, LIMIT);
  if (localStorage.getItem(LABELLED_KEY) !== null)
    return storedArray(LABELLED_KEY)
      .flatMap((entry): RecentFolder[] =>
        Array.isArray(entry) &&
        entry.length === 2 &&
        typeof entry[0] === "string" &&
        entry[0].length > 0 &&
        typeof entry[1] === "string"
          ? [{ id: null, path: entry[0], label: entry[1] }]
          : [],
      )
      .slice(0, LIMIT);
  return storedArray(LEGACY_KEY)
    .flatMap((entry): RecentFolder[] =>
      typeof entry === "string" && entry.length > 0
        ? [{ id: null, path: entry, label: entry.split("/").at(-1) ?? entry }]
        : [],
    )
    .slice(0, LIMIT);
}
// oxlint-enable anti-slop/no-runtime-typeof

export function collectionIdentity(params: URLSearchParams): number | null {
  const raw = params.get("cid");
  if (!params.get("c") || raw === null || !/^[1-9]\d*$/.test(raw)) return null;
  const id = Number(raw);
  return Number.isSafeInteger(id) ? id : null;
}

/** A route entry is revalidated even if a previously visited ID is cached. */
export function useCollectionDestination(path: string | null, id: number | null) {
  const client = useQueryClient();
  const api = useQueryApi();
  return useQuery({
    ...(id === null
      ? collectionLookupOptions(path, client, api)
      : collectionLookupByIdOptions(id, client, api)),
    enabled: path !== null,
    staleTime: 0,
    refetchOnMount: "always",
    retry: false,
  });
}

/** Six persisted identities are resolved only when the menu opens. */
export function useRecentFolders(current: CollectionLookupRead | undefined, open: boolean) {
  const client = useQueryClient();
  const api = useQueryApi();
  const [folders, setFolders] = useState(readRecentFolders);
  const resolutions = useQueries({
    queries: folders.map((folder) => ({
      ...(folder.id === null
        ? collectionLookupOptions(folder.path, client, api)
        : collectionLookupByIdOptions(folder.id, client, api)),
      enabled: open,
      staleTime: 0,
      retry: false,
    })),
  });
  const [recordedVisit, setRecordedVisit] = useState<CollectionLookupRead>();
  const visitChanged = current !== undefined && current !== recordedVisit;
  if (visitChanged) {
    setRecordedVisit(current);
    const { collection } = current;
    const next = { id: collection.id, path: collection.path, label: collection.display_path };
    if (
      folders[0]?.id !== next.id ||
      folders[0]?.path !== next.path ||
      folders[0]?.label !== next.label
    )
      setFolders(
        [
          next,
          ...folders.filter((folder) => folder.id !== next.id && folder.path !== next.path),
        ].slice(0, LIMIT),
      );
  }
  useEffect(() => {
    localStorage.setItem(
      IDENTIFIED_KEY,
      JSON.stringify(folders.map((folder) => [folder.id, folder.path, folder.label])),
    );
  }, [folders]);
  const resolved = folders.map((folder, index) => {
    const query = resolutions[index];
    const collection = !query.isFetching && !query.error ? query.data?.collection : undefined;
    return {
      ...(collection
        ? { id: collection.id, path: collection.path, label: collection.display_path }
        : folder),
      unavailable: query.isError,
    };
  });
  // Persist only server-confirmed paths. IDs survive parent renames, moves and reloads.
  // Record a new visit first; its reordered list is resolved on the next render.
  if (
    open &&
    !visitChanged &&
    resolved.some(
      (folder, index) =>
        folder.id !== folders[index].id ||
        folder.path !== folders[index].path ||
        folder.label !== folders[index].label,
    )
  )
    setFolders(resolved.map(({ id, path, label }) => ({ id, path, label })));
  return { folders: resolved, clear: () => setFolders([]) };
}
