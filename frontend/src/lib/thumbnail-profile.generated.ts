// Generated from printstash-core/mesh/preview_profile.json. Do not edit by hand.
export const THUMBNAIL_PROFILE = {
  version: 8,
  aspectRatio: [4, 3],
  background: "transparent",
  upAxis: "z",
  hero: { azimuthDegrees: -35, elevationDegrees: 30 },
  flat: { tiltDegrees: 25, thicknessRatio: 0.15 },
  marginFraction: 0.1,
  supersampling: { maxOutputWidth: 640, smallFactor: 2, largeFactor: 1 },
  encoding: {
    format: "webp",
    lossless: false,
    quality: 85,
    alphaQuality: 100,
    exact: false,
    method: 2,
  },
} as const;
export const PREVIEW_LOOK_COLOURS = {
  "sky": "#9FB8DA",
  "printstash_blue": "#5E8BCB",
  "slate": "#8E97A6",
  "graphite": "#6E737C",
  "sand": "#CDBCA8"
} as const;
