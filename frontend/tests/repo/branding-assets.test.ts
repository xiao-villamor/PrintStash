/*
 * The favicon follows the theme, and its URL is versioned.
 *
 * A favicon is the single most aggressively cached asset a browser holds. Without
 * a version in the URL, a rebranded icon never reaches anybody who has visited
 * before — which is most users. The dark-mode variant matters because a dark icon
 * on a dark tab strip is invisible, and the tab is how people find PrintStash
 * among twenty others.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";
import { brandMarkStrokes } from "../../src/components/auth-print-geometry";

const root = resolve(import.meta.dirname, "../..");

describe("faviconFor", () => {
  it("uses current blue brand in dark mode and versioned asset URLs", () => {
    const darkIcon = readFileSync(resolve(root, "public/icon-dark.svg"), "utf8");
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    const bootstrap = readFileSync(resolve(root, "public/theme-bootstrap.js"), "utf8");
    const theme = readFileSync(resolve(root, "src/lib/theme.ts"), "utf8");

    expect(darkIcon).toContain("#2767FF");
    expect(darkIcon).toContain("#0E48F0");
    expect(darkIcon).not.toMatch(/#fb923c|#ea580c/i);
    expect(html).toContain("/theme-bootstrap.js");
    expect(bootstrap).toContain("/icon-dark.svg?v=4");
    expect(theme).toContain("/icon-dark.svg?v=4");
  });
});

describe("PrintStash monogram", () => {
  it.each([
    "public/icon-light.svg",
    "public/icon-dark.svg",
    "public/logo.svg",
    "public/logo-dark.svg",
    "../browser-extension/icon.svg",
  ])("uses the same shape in %s", (path) => {
    const asset = readFileSync(resolve(root, path), "utf8");
    for (const stroke of brandMarkStrokes) expect(asset).toContain(`d="${stroke.d}"`);
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
