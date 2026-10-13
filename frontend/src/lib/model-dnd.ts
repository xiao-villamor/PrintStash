import { getSessionVersion } from "./session-transport";
import type { OutlinerModelRead } from "@/types";

// Internal model moves are distinct from OS file-upload drags.
export const MODEL_DND_MIME = "application/x-printstash-model";
export type ModelDrag = Readonly<
  Pick<OutlinerModelRead, "id" | "edit_epoch" | "edit_version" | "name" | "collection"> & {
    session: number;
  }
>;

/** Freeze the displayed editing base at the gesture, including for paginated tree leaves. */
export function captureModelDrag(
  model: Pick<OutlinerModelRead, "id" | "edit_epoch" | "edit_version" | "name" | "collection">,
): ModelDrag {
  return {
    id: model.id,
    edit_epoch: model.edit_epoch,
    edit_version: model.edit_version,
    name: model.name,
    collection: model.collection,
    session: getSessionVersion(),
  };
}

/** DataTransfer is foreign input: reject legacy id-only and retired-session gestures. */
// oxlint-disable anti-slop/no-runtime-typeof -- Decode the browser DataTransfer JSON at its boundary; every field is checked before a ModelDrag escapes.
export function readModelDrag(raw: string): ModelDrag | null {
  try {
    const value: unknown = JSON.parse(raw);
    if (
      value === null ||
      typeof value !== "object" ||
      !("id" in value) ||
      typeof value.id !== "number" ||
      !Number.isSafeInteger(value.id) ||
      value.id < 1 ||
      !("edit_epoch" in value) ||
      typeof value.edit_epoch !== "string" ||
      !/^[0-9a-f]{32}$/.test(value.edit_epoch) ||
      !("edit_version" in value) ||
      typeof value.edit_version !== "number" ||
      !Number.isSafeInteger(value.edit_version) ||
      value.edit_version < 1 ||
      !("name" in value) ||
      typeof value.name !== "string" ||
      !("collection" in value) ||
      (value.collection !== null && typeof value.collection !== "string") ||
      !("session" in value) ||
      value.session !== getSessionVersion()
    )
      return null;
    return {
      id: value.id,
      edit_epoch: value.edit_epoch,
      edit_version: value.edit_version,
      name: value.name,
      collection: value.collection,
      session: value.session,
    };
  } catch {
    return null;
  }
}
// oxlint-enable anti-slop/no-runtime-typeof
