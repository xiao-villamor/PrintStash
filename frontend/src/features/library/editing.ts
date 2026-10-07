import type { EditingBase } from "@/types/editing";

/** Counters order one history only. A request may replace the history it observed
 * before starting, but never a different history installed while it was in flight. */
export function acceptsEditingSnapshot(
  current: EditingBase | undefined,
  next: EditingBase,
  observedEpoch: string | null,
): boolean {
  if (!current) return true;
  if (current.edit_epoch === next.edit_epoch) return next.edit_version >= current.edit_version;
  return current.edit_epoch === observedEpoch;
}
