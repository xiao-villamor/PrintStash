import * as THREE from "three";

import { THUMBNAIL_PROFILE } from "@/lib/thumbnail-profile.generated";

const SCREENSHOT_PIXEL_BUDGET = 16_000_000;
const WORLD_UP = new THREE.Vector3(0, 1, 0);

export interface CameraFit {
  distance: number;
  position: THREE.Vector3;
  projectedWidth: number;
  projectedHeight: number;
}

export interface ScreenshotDimensions {
  width: number;
  height: number;
}

const TRANSPARENT_BACKGROUNDS = new Set(["transparent", "rgba(0, 0, 0, 0)"]);

export function visibleCanvasBackground(element: HTMLElement): THREE.Color | null {
  let current: HTMLElement | null = element;
  while (current) {
    const value = window.getComputedStyle(current).backgroundColor;
    if (value && !TRANSPARENT_BACKGROUNDS.has(value)) {
      return new THREE.Color(value);
    }
    current = current.parentElement;
  }
  return null;
}

export function heroCameraDirection(): THREE.Vector3 {
  const azimuth = THREE.MathUtils.degToRad(THUMBNAIL_PROFILE.hero.azimuthDegrees);
  const elevation = THREE.MathUtils.degToRad(THUMBNAIL_PROFILE.hero.elevationDegrees);
  const horizontal = Math.cos(elevation);
  return new THREE.Vector3(
    Math.sin(azimuth) * horizontal,
    Math.sin(elevation),
    -Math.cos(azimuth) * horizontal,
  ).normalize();
}

export function fitCameraToBounds(
  size: THREE.Vector3,
  aspect: number,
  verticalFovDegrees: number,
  direction = heroCameraDirection(),
): CameraFit {
  const safeAspect = Math.max(aspect, 1e-6);
  const forward = direction.clone().normalize().negate();
  const right = new THREE.Vector3().crossVectors(forward, WORLD_UP).normalize();
  const screenUp = new THREE.Vector3().crossVectors(right, forward).normalize();
  const half = size.clone().multiplyScalar(0.5);
  let halfWidth = 0;
  let halfHeight = 0;
  let halfDepth = 0;
  for (const x of [-half.x, half.x]) {
    for (const y of [-half.y, half.y]) {
      for (const z of [-half.z, half.z]) {
        const corner = new THREE.Vector3(x, y, z);
        halfWidth = Math.max(halfWidth, Math.abs(corner.dot(right)));
        halfHeight = Math.max(halfHeight, Math.abs(corner.dot(screenUp)));
        halfDepth = Math.max(halfDepth, Math.abs(corner.dot(forward)));
      }
    }
  }

  const contentScale = 1 - 2 * THUMBNAIL_PROFILE.marginFraction;
  const projectedWidth = (halfWidth * 2) / contentScale;
  const projectedHeight = (halfHeight * 2) / contentScale;
  const verticalFov = THREE.MathUtils.degToRad(verticalFovDegrees);
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * safeAspect);
  const distance =
    Math.max(
      projectedHeight / 2 / Math.tan(verticalFov / 2),
      projectedWidth / 2 / Math.tan(horizontalFov / 2),
    ) + halfDepth;

  return {
    distance,
    position: direction.clone().normalize().multiplyScalar(distance),
    projectedWidth,
    projectedHeight,
  };
}

export function screenshotDimensions(
  cssWidth: number,
  cssHeight: number,
  scale: number,
  maxTextureSize: number,
): ScreenshotDimensions {
  const width = Math.max(cssWidth, 1);
  const height = Math.max(cssHeight, 1);
  const requestedScale = Math.max(scale, 1);
  const textureScale = maxTextureSize / Math.max(width, height);
  const pixelScale = Math.sqrt(SCREENSHOT_PIXEL_BUDGET / (width * height));
  const effectiveScale = Math.min(requestedScale, textureScale, pixelScale);
  return {
    width: Math.max(1, Math.floor(width * effectiveScale)),
    height: Math.max(1, Math.floor(height * effectiveScale)),
  };
}

export function screenshotHasForeground(pixels: Uint8Array): boolean {
  if (pixels.length < 8 || pixels.length % 4 !== 0) return false;
  const red = pixels[0];
  const green = pixels[1];
  const blue = pixels[2];
  const alpha = pixels[3];
  for (let index = 4; index < pixels.length; index += 4) {
    if (
      pixels[index] !== red ||
      pixels[index + 1] !== green ||
      pixels[index + 2] !== blue ||
      pixels[index + 3] !== alpha
    ) {
      return true;
    }
  }
  return false;
}
