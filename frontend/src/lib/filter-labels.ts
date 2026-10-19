import { currentLocale, knownUiText } from "./locale";
import { formatDuration } from "./format";
import type { ArtifactFileType } from "@/types";

/** File formats are written the way they name themselves, in every locale. */
const FILE_TYPE_TEXT = {
  stl: "STL",
  "3mf": "3MF",
  obj: "OBJ",
  step: "STEP",
  dxf: "DXF",
  gcode: "G-code",
} as const satisfies Record<ArtifactFileType, string>;

function isFileTypeValue(value: string): value is ArtifactFileType {
  return Object.hasOwn(FILE_TYPE_TEXT, value);
}

/** A controlled value reads as a label in the panel and on its chip alike: "Needs test". */
function sentenceCase(text: string): string {
  return text.charAt(0).toLocaleUpperCase(currentLocale()) + text.slice(1);
}

/** Only controlled facet values are UI enums; material/printer names remain data. */
export function filterValueText(key: string, value: string): string {
  if (key === "file_type" && isFileTypeValue(value)) return FILE_TYPE_TEXT[value];
  if (key === "print_duration_min_s" || key === "print_duration_max_s") {
    const seconds = Number(value);
    if (Number.isFinite(seconds) && seconds >= 0) {
      return seconds === 0 ? "0s" : formatDuration(seconds);
    }
  }
  return ["revision_status", "print_outcome", "storage", "printed"].includes(key)
    ? sentenceCase(knownUiText(value))
    : value;
}
