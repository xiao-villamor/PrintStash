import type { FileRead } from "@/types";

export function hasToolpath(file: Pick<FileRead, "file_type" | "original_filename">) {
  return (
    file.file_type === "gcode" ||
    (file.file_type === "3mf" && file.original_filename.toLowerCase().endsWith(".gcode.3mf"))
  );
}
