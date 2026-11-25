/*
 * Published assets share the UI mark. Small theme-aware favicons use the
 * silhouette alone so the diagonal gap remains distinct at tab size.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { brandMarkFold, brandMarkOutline } from "../../src/components/brand-geometry";

const root = resolve(import.meta.dirname, "../..");

describe("favicons", () => {
  it.each([
    ["light", "#141923"],
    ["dark", "#f1f4fa"],
  ])("renders a single-tone silhouette in %s mode", (theme, ink) => {
    const asset = readFileSync(resolve(root, `public/icon-${theme}.svg`), "utf8");
    expect(asset).toContain(`<path fill="${ink}" d="${brandMarkOutline}"/>`);
    expect(asset.match(/<path\b/g)).toHaveLength(1);
  });

  it.each([
    ["light", "#f6f7f9"],
    ["dark", "#13171e"],
  ])("provides an opaque neutral tile for the %s icon", (theme, background) => {
    const asset = readFileSync(resolve(root, `public/icon-${theme}.svg`), "utf8");
    expect(asset).toContain(`<rect width="24" height="24" rx="5" fill="${background}"/>`);
  });
});

describe("PrintStash monogram", () => {
  it.each([
    "public/logo.svg",
    "public/logo-dark.svg",
    "public/icon-maskable.svg",
    "../browser-extension/icon.svg",
  ])("uses the same shape in %s", (path) => {
    const asset = readFileSync(resolve(root, path), "utf8");
    expect(asset).toContain(`d="${brandMarkOutline}"`);
    expect(asset).toContain(`d="${brandMarkFold}"`);
  });

  it.each([16, 32, 48, 128])("exports a square %i px extension icon", (size) => {
    const icon = readFileSync(resolve(root, `../browser-extension/icon-${size}.png`));
    expect(icon.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(icon.readUInt32BE(16)).toBe(size);
    expect(icon.readUInt32BE(20)).toBe(size);
  });
});

describe("Docker icon", () => {
  it("provides the existing square brand mark as a public PNG", () => {
    const icon = readFileSync(resolve(root, "public/logo.png"));
    const extensionIcon = readFileSync(resolve(root, "../browser-extension/icon-128.png"));

    expect(icon.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
    expect(icon.readUInt32BE(16)).toBe(128);
    expect(icon.readUInt32BE(20)).toBe(128);
    expect(icon).toEqual(extensionIcon);
  });
});
