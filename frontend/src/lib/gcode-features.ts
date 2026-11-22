/**
 * Print features named by slicer comments, coloured the way slicers show them.
 *
 * PrusaSlicer, OrcaSlicer, Bambu Studio and Cura mark each move's feature with
 * `;TYPE:<name>` (Orca and Bambu also write `; FEATURE: <name>`). Their names
 * differ, so each maps onto one closed set the viewer colours and filters.
 */

export const TOOLPATH_FEATURES = [
  "outer_wall",
  "inner_wall",
  "overhang_wall",
  "top_bottom",
  "solid_infill",
  "sparse_infill",
  "bridge",
  "support",
  "skirt_brim",
  "other",
] as const;

export type ToolpathFeature = (typeof TOOLPATH_FEATURES)[number];

/** Index of a feature in `TOOLPATH_FEATURES`, as stored per vertex. */
export const featureIndex = (feature: ToolpathFeature) => TOOLPATH_FEATURES.indexOf(feature);

/** Muted sRGB colours, distinguishable on the viewer's dark stage. */
export const TOOLPATH_FEATURE_COLOURS = {
  outer_wall: "#E9A15F",
  inner_wall: "#E3CB7D",
  overhang_wall: "#5FB8A9",
  top_bottom: "#D8695C",
  solid_infill: "#C47BA6",
  sparse_infill: "#9D82C9",
  bridge: "#6C9BD2",
  support: "#7FB069",
  skirt_brim: "#8FA3B8",
  other: "#8A929C",
} as const satisfies Record<ToolpathFeature, string>;

/** Matches `;TYPE:External perimeter` and `; FEATURE: Outer wall`. */
const MARKER = /^\s*;\s*(?:TYPE|FEATURE)\s*:\s*(.+?)\s*$/i;

/** The slicer feature a comment line names, or null when it names none. */
export function featureMarker(line: string): ToolpathFeature | null {
  const match = MARKER.exec(line);
  return match ? classifyFeature(match[1]!) : null;
}

/** Map one slicer's feature name onto the shared set. Order matters. */
export function classifyFeature(name: string): ToolpathFeature {
  const value = name.toLowerCase();
  if (value.includes("support")) return "support";
  if (
    value.includes("external perimeter") ||
    value.includes("outer wall") ||
    value === "wall-outer"
  )
    return "outer_wall";
  if (value.includes("overhang")) return "overhang_wall";
  if (value.includes("perimeter") || value.includes("wall")) return "inner_wall";
  if (value.includes("bridge")) return "bridge";
  if (
    value.includes("top") ||
    value.includes("bottom") ||
    value.includes("ironing") ||
    value === "skin"
  )
    return "top_bottom";
  if (value.includes("solid") || value.includes("gap")) return "solid_infill";
  if (value.includes("infill") || value === "fill") return "sparse_infill";
  if (value.includes("skirt") || value.includes("brim")) return "skirt_brim";
  return "other";
}

const linear = (channel: number) =>
  channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;

/** A hex sRGB colour as linear 0–255 channels, as WebGL vertex colours expect. */
export function linearRgb(hex: string): [number, number, number] {
  const value = Number.parseInt(hex.slice(1), 16);
  const channel = (shift: number) => Math.round(linear(((value >> shift) & 255) / 255) * 255);
  return [channel(16), channel(8), channel(0)];
}
