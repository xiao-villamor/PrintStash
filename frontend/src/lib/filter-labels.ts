import { knownUiText } from "./locale";

/** Only controlled facet values are UI enums; material/printer names remain data. */
export function filterValueText(key: string, value: string): string {
  return ["revision_status", "print_outcome", "storage", "printed"].includes(key)
    ? knownUiText(value)
    : value;
}
