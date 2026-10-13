import { useState } from "react";
import { useLocation } from "react-router-dom";
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

/** The drawer and visible tree share one restoration for each history entry. */
export function useOutlinerRestoration(
  params: OutlinerParams,
  selectedCollection: string | null,
  enabled: boolean,
  expanded?: ReadonlySet<string>,
) {
  const location = useLocation();
  const snapshot = () => ({
    entry: location.key,
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
  if (target.entry !== location.key) setTarget(snapshot());
  const { entry: _entry, ...restoreParams } = target;
  const restoreEnabled = enabled && restoreParams.expanded_paths.length > 0;
  const restore = useOutlinerRestore({ ...params, ...restoreParams }, restoreEnabled);
  return { restore, enabled: restoreEnabled, restoring: restoreEnabled && restore.isPending };
}
