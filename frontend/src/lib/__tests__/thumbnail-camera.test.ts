/*
 * The canonical preview camera stays above the build plane, fits projected
 * model bounds for the real viewport, and bounds screenshot resource usage.
 */

import { describe, expect, it } from "vitest";
import * as THREE from "three";

import {
  fitCameraToBounds,
  heroCameraDirection,
  screenshotHasForeground,
  screenshotDimensions,
  visibleCanvasBackground,
  viewerPresetDirection,
} from "@/lib/thumbnail-camera";

describe("thumbnail camera", () => {
  it("keeps the hero camera above the build plane", () => {
    const direction = heroCameraDirection();

    expect(direction.y).toBeCloseTo(0.5);
    expect(direction.length()).toBeCloseTo(1);
    // Same object-to-camera direction as printstash-core after the viewer's
    // Z-up mesh is mapped into Three.js' Y-up scene.
    expect(direction.x).toBeCloseTo(-0.4967, 3);
    expect(direction.z).toBeCloseTo(-0.7094, 3);
  });

  it("fits toolpath isometric views from the same direction as the source mesh", () => {
    const size = new THREE.Vector3(240, 150, 250);
    const source = fitCameraToBounds(size, 1.6, 45).position.normalize();
    const toolpath = fitCameraToBounds(
      size,
      1.6,
      45,
      viewerPresetDirection("isometric"),
    ).position.normalize();

    expect(toolpath.distanceTo(source)).toBeLessThan(1e-10);
  });

  it("keeps the front view on the common front axis", () => {
    expect(viewerPresetDirection("front").toArray()).toEqual([0, 0, 1]);
  });

  it("keeps top-view fitting finite and nearly overhead", () => {
    const size = new THREE.Vector3(240, 150, 250);
    const camera = fitCameraToBounds(size, 1.6, 45, viewerPresetDirection("top"));

    expect(Number.isFinite(camera.distance)).toBe(true);
    expect(camera.position.y).toBeGreaterThan(0);
    expect(camera.position.z / camera.position.y).toBeCloseTo(0.001, 5);
  });

  it.each([
    [new THREE.Vector3(2, 20, 2), 16 / 9],
    [new THREE.Vector3(20, 2, 2), 9 / 16],
  ])("fits projected bounds for each viewport aspect", (size, aspect) => {
    const fit = fitCameraToBounds(size, aspect, 50);
    const verticalFov = THREE.MathUtils.degToRad(50);
    const visibleHeight = 2 * fit.distance * Math.tan(verticalFov / 2);
    const visibleWidth = visibleHeight * aspect;

    expect(visibleHeight).toBeGreaterThanOrEqual(fit.projectedHeight);
    expect(visibleWidth).toBeGreaterThanOrEqual(fit.projectedWidth);
    expect(fit.position.y).toBeGreaterThan(0);
  });

  it("caps screenshot dimensions within both resource ceilings", () => {
    const result = screenshotDimensions(4000, 3000, 3, 8192);

    expect(result.width).toBeLessThanOrEqual(8192);
    expect(result.height).toBeLessThanOrEqual(8192);
    expect(result.width * result.height).toBeLessThanOrEqual(16_000_000);
    expect(result.width / result.height).toBeCloseTo(4 / 3, 2);
  });

  it("rejects empty uniform screenshot buffers", () => {
    expect(screenshotHasForeground(new Uint8Array(16))).toBe(false);
    expect(screenshotHasForeground(new Uint8Array([0, 0, 0, 0, 0, 0, 0, 255]))).toBe(true);
  });

  it("captures the first visible ancestor background for WYSIWYG screenshots", () => {
    const surface = document.createElement("div");
    surface.style.backgroundColor = "rgb(12, 34, 56)";
    const transparentWrapper = document.createElement("div");
    const canvas = document.createElement("canvas");
    transparentWrapper.appendChild(canvas);
    surface.appendChild(transparentWrapper);
    document.body.appendChild(surface);

    expect(visibleCanvasBackground(canvas)?.getHexString()).toBe("0c2238");

    surface.remove();
  });
});
