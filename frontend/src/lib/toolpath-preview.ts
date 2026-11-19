import { useQueries } from "@tanstack/react-query";
import { gcodePlatesOptions } from "@/lib/queries/file-plates";
import type { FileRead } from "@/types";

export type ToolpathAvailability = "available" | "checking" | "unavailable" | "error";

/** Revisions already declare their format. Older source 3MFs may also carry
 * sliced plates, which the existing file-scoped query can confirm. */
export function useToolpathSources(files: FileRead[], active = true) {
  const archives = files.filter((file) => file.file_type === "3mf");
  const plates = useQueries({
    queries: archives.map((file) => ({ ...gcodePlatesOptions(file.id), enabled: active })),
  });
  const embedded = new Set(
    archives.flatMap((file, index) => (plates[index]?.data?.plates.length ? [file.id] : [])),
  );
  const sources = files.filter((file) => file.file_type === "gcode" || embedded.has(file.id));
  const availability: ToolpathAvailability =
    sources.length > 0
      ? "available"
      : plates.some((query) => query.isPending)
        ? "checking"
        : plates.some((query) => query.isError)
          ? "error"
          : "unavailable";
  return { sources, availability };
}
