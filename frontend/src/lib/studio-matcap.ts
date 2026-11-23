import { CanvasTexture, SRGBColorSpace } from "three";
import { PREVIEW_STUDIO } from "@/lib/thumbnail-profile.generated";
import type { PreviewLook } from "@/types/config";

/** The look a library without a stored choice renders with. */
export const DEFAULT_PREVIEW_LOOK: PreviewLook = "sky";

type Vector = readonly [number, number, number];

function normalise([x, y, z]: Vector): Vector {
  const length = Math.hypot(x, y, z);
  return [x / length, y / length, z / length];
}

const dot = (a: Vector, b: Vector) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const clamp = (value: number) => Math.min(1, Math.max(0, value));

const KEY = normalise(PREVIEW_STUDIO.key.direction);
const FILL = normalise(PREVIEW_STUDIO.fill.direction);
const HALF = normalise([KEY[0], KEY[1], KEY[2] + 1]);

/**
 * The generated images' studio shade for a unit normal in the camera frame:
 * diffuse key and fill over ambient, a Fresnel rim and a Blinn-Phong highlight.
 * A port of printstash-core's `make_preview_shade`, so the 3D view and the
 * card thumbnail colour a surface facing the same way identically.
 */
export function studioShade(look: PreviewLook, normal: Vector): Vector {
  const style = PREVIEW_STUDIO.looks[look];
  const key = clamp(dot(normal, KEY));
  const fill = clamp(dot(normal, FILL));
  const fresnel = (1 - clamp(normal[2])) ** PREVIEW_STUDIO.rim.power;
  const specular = clamp(dot(normal, HALF)) ** PREVIEW_STUDIO.specularPower;
  const channel = (index: 0 | 1 | 2) =>
    clamp(
      (style.ambient +
        style.key * key * PREVIEW_STUDIO.key.colour[index] +
        style.fill * fill * PREVIEW_STUDIO.fill.colour[index]) *
        style.albedo[index] +
        style.rim * fresnel * PREVIEW_STUDIO.rim.colour[index] +
        style.specular * specular,
    );
  return [channel(0), channel(1), channel(2)];
}

const MATCAP_SIZE = 256;
// three's matcap lookup maps a view-space normal to uv = n.xy * 0.495 + 0.5.
const MATCAP_SCALE = 0.495;
const matcaps = new Map<PreviewLook, CanvasTexture>();

/** A matcap holding the studio shade of every camera-facing normal, per look. */
export function studioMatcap(look: PreviewLook): CanvasTexture {
  const cached = matcaps.get(look);
  if (cached) return cached;
  const canvas = document.createElement("canvas");
  canvas.width = MATCAP_SIZE;
  canvas.height = MATCAP_SIZE;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("studio_matcap_unavailable");
  const image = context.createImageData(MATCAP_SIZE, MATCAP_SIZE);
  for (let row = 0; row < MATCAP_SIZE; row += 1) {
    // Canvas rows run downwards; the texture's flipY puts row 0 at uv.y = 1.
    const y = (1 - (row + 0.5) / MATCAP_SIZE - 0.5) / MATCAP_SCALE;
    for (let column = 0; column < MATCAP_SIZE; column += 1) {
      const x = ((column + 0.5) / MATCAP_SIZE - 0.5) / MATCAP_SCALE;
      const planar = Math.min(1, Math.hypot(x, y));
      const scale = planar > 0 ? planar / Math.hypot(x, y) : 0;
      const normal: Vector = [x * scale, y * scale, Math.sqrt(1 - planar * planar)];
      const [r, g, b] = studioShade(look, normal);
      const offset = (row * MATCAP_SIZE + column) * 4;
      image.data[offset] = Math.round(r * 255);
      image.data[offset + 1] = Math.round(g * 255);
      image.data[offset + 2] = Math.round(b * 255);
      image.data[offset + 3] = 255;
    }
  }
  context.putImageData(image, 0, 0);
  const texture = new CanvasTexture(canvas);
  // The studio writes display values: decode them so output encoding restores them.
  texture.colorSpace = SRGBColorSpace;
  matcaps.set(look, texture);
  return texture;
}
