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
  const sources = files.filter(
    (file) =>
      file.file_type === "gcode" || file.sliced_geometry === "gcode_only" || embedded.has(file.id),
  );
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

/** A printer's usable bed footprint in millimetres. */
interface BedSize {
  x: number;
  y: number;
}

export function getBedSize(printerModel: string | null | undefined): BedSize | null {
  if (!printerModel) return null;
  const m = printerModel.toLowerCase();
  if (m.includes("a1 mini") || (m.includes("bambu") && m.includes("mini")))
    return { x: 180, y: 180 };
  if (m.includes("bambu") || m.includes("x1") || m.includes("p1s") || m.includes("a1"))
    return { x: 256, y: 256 };
  if (m.includes("prusa") && m.includes("mini")) return { x: 180, y: 180 };
  if (m.includes("prusa") || m.includes("mk3") || m.includes("mk4")) return { x: 250, y: 210 };
  if (m.includes("voron") || m.includes("v2.4")) {
    if (m.includes("350")) return { x: 350, y: 350 };
    if (m.includes("300")) return { x: 300, y: 300 };
    return null;
  }
  if (m.includes("cr-10") || m.includes("cr10")) return { x: 300, y: 300 };
  if (m.includes("ender") || m.includes("k1")) return { x: 220, y: 220 };
  return null;
}
