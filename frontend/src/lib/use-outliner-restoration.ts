import { useState } from "react";
import { useLocation } from "@tanstack/react-router";
import { ancestorPaths } from "@/lib/collection-tree";
import { useOutlinerRestore } from "@/lib/queries";
import type { OutlinerParams } from "@/types/outliner";

export const EXPANDED_KEY = "ps-filter-expanded";

/** Session preferences are hints; malformed storage starts with a closed tree. */
export function readExpandedPaths(): Set<string> | null {
  try {
    const saved = sessionStorage.getItem(EXPANDED_KEY);
    if (!saved) return null;
    const parsed: unknown = JSON.parse(saved);
    return Array.isArray(parsed) ? new Set(parsed.map(String)) : null;
  } catch {
    return null;
  }
}

/** Folders opened outside the active path that a reload brings back. */
export const REMEMBERED_EXPANSIONS = 6;

/**
 * What a reload reopens: the folders on the way to the current collection,
 * always, plus the most recently opened of the others. The saved set keeps its
 * insertion order, so a folder opened or revealed later sorts later; without
 * the limit a long session restored a tree as tall as the whole library.
 */
export function restoredExpansions(
  saved: ReadonlySet<string> | null,
  ancestors: readonly string[],
  limit = REMEMBERED_EXPANSIONS,
): Set<string> {
  const onPath = new Set(ancestors);
  const others = [...(saved ?? [])].filter((path) => !onPath.has(path));
  return new Set([...others.slice(Math.max(0, others.length - limit)), ...ancestors]);
}

/** The drawer and visible tree share one restoration for each history entry. */
export function useOutlinerRestoration(
  params: OutlinerParams,
  selectedCollection: string | null,
  enabled: boolean,
  expanded?: ReadonlySet<string>,
) {
  const location = useLocation();
  const snapshot = () => ({
    entry: location.state.__TSR_key,
    expanded_paths: [
      ...new Set([
        ...(expanded ?? readExpandedPaths() ?? []),
        ...ancestorPaths(selectedCollection ?? ""),
      ]),
    ]
      .filter(Boolean)
      .sort(),
    selected_path: selectedCollection,
  });
  const [target, setTarget] = useState(snapshot);
  if (target.entry !== location.state.__TSR_key) setTarget(snapshot());
  const { entry: _entry, ...restoreParams } = target;
  const restoreEnabled = enabled && restoreParams.expanded_paths.length > 0;
  const restore = useOutlinerRestore({ ...params, ...restoreParams }, restoreEnabled);
  return { restore, enabled: restoreEnabled, restoring: restoreEnabled && restore.isPending };
}
