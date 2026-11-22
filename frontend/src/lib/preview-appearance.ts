import type { PreviewAppearance } from "@/types/config";

export function appearanceChanged(saved: PreviewAppearance, draft: PreviewAppearance): boolean {
  return (
    saved.preview_look !== draft.preview_look ||
    saved.preview_floor_grid !== draft.preview_floor_grid
  );
}
