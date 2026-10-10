import { knownUiText } from "./locale";
import { formatDuration } from "./format";
import type { ArtifactFileType } from "@/types";

/** How each `FileType` is written by the formats themselves: "STL", not "Stl". */
const FILE_TYPE_TEXT = {
  stl: "STL",
  "3mf": "3MF",
  obj: "OBJ",
  step: "STEP",
  dxf: "DXF",
  gcode: "G-code",
} satisfies Record<ArtifactFileType, string>;

function isArtifactFileType(value: string): value is ArtifactFileType {
  return Object.hasOwn(FILE_TYPE_TEXT, value);
}

/** Only controlled facet values are UI enums; material/printer names remain data. */
export function filterValueText(key: string, value: string): string {
  if (key === "print_duration_min_s" || key === "print_duration_max_s") {
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return seconds === 0 ? "0s" : formatDuration(seconds);
    }
  }
  if (key === "file_type") {
    return isArtifactFileType(value) ? FILE_TYPE_TEXT[value] : value.toUpperCase();
  }
  return ["revision_status", "print_outcome", "storage", "printed"].includes(key)
    ? knownUiText(value)
    : value;
}
