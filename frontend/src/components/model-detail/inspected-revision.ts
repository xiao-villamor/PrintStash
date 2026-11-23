import type { FileRead } from "@/types";

/** Inspection is local navigation; it never changes a revision's recommendation. */
export function inspectedRevision(
  files: readonly FileRead[],
  selectedId: number | null,
): FileRead | null {
  const selected = files.find((file) => file.id === selectedId);
  return selected ?? files.find((file) => file.is_recommended) ?? files.at(-1) ?? null;
}
