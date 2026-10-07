import type { EditingBase } from "@/types/editing";

/** Validate the public editing protocol at its transport boundary. */
export function requireEditingBase(base: EditingBase): void {
  if (
    !base ||
    !/^[0-9a-f]{32}$/.test(base.edit_epoch) ||
    !Number.isSafeInteger(base.edit_version) ||
    base.edit_version < 1
  )
    throw new Error("Invalid editing base");
}

export function editHeaders(
  kind: "model" | "multipart" | "document",
  id: number,
  base: EditingBase,
) {
  requireEditingBase(base);
  return {
    "If-Match": `"${kind}-${id}-e${base.edit_epoch}-v${base.edit_version}"`,
    "X-PrintStash-Edit-Contract": "conditional-v1",
  };
}

/** Never confirm a response from a different history or a non-advancing write. */
export function requireEditingReceipt(saved: EditingBase, base: EditingBase): void {
  requireEditingBase(saved);
  if (saved.edit_epoch !== base.edit_epoch || saved.edit_version <= base.edit_version)
    throw new Error("Invalid editing acknowledgement");
}

/** Copy the immutable editing pair when a user starts an intent. */
export function captureEditingBase(snapshot: EditingBase): EditingBase {
  requireEditingBase(snapshot);
  return { edit_epoch: snapshot.edit_epoch, edit_version: snapshot.edit_version };
}
