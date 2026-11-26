import type { PreviewAppearance, PreviewLook, PreviewMode } from "@/types/config";

export const previewLookLabel = {
  sky: "Sky",
  printstash_blue: "Blue",
  slate: "Slate",
  graphite: "Graphite",
  sand: "Sand",
  sage: "Sage",
  clay: "Clay",
  plum: "Plum",
} as const satisfies Record<PreviewLook, string>;

export const previewViewLabel = {
  single: "setup.previewViewsSingle",
  oblique: "setup.previewViewsAngles",
  "oblique-poles": "setup.previewViewsFull",
} as const satisfies Record<PreviewMode, string>;

export function appearanceChanged(saved: PreviewAppearance, draft: PreviewAppearance): boolean {
  return (
    saved.preview_look !== draft.preview_look ||
    saved.preview_floor_grid !== draft.preview_floor_grid
  );
}
