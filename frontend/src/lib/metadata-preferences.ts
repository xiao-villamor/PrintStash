export * from "@printstash/domain/metadata-preferences";
import { METADATA_FIELDS as fields } from "@printstash/domain/metadata-preferences";
import { knownUiText } from "./locale";

export const METADATA_FIELDS = fields.map((field) => ({
  ...field,
  get label() {
    return knownUiText(field.label);
  },
}));
