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

describe("README logo", () => {
  const readme = readFileSync(resolve(root, "../README.md"), "utf8");

  it("displays the project name once at the top", () => {
    expect(readme).toContain('alt="PrintStash logo"');
    expect(readme).not.toMatch(/^# PrintStash\s*$/m);
  });

  it("renders the slogan as README text", () => {
    expect(readme).toContain("**Your 3D printing workflow in one place.**");
  });

  describe.each(["light", "dark"])("in %s mode", (theme) => {
    const file = `screenshots/printstash-logo-${theme}.gif`;
    const asset = readFileSync(resolve(root, "..", file));
    // Each graphics-control extension carries the transparency and delay for a frame.
    const graphicsControlMarker = Buffer.from([0x21, 0xf9, 0x04]);
    const frameControls: number[] = [];
    for (
      let offset = asset.indexOf(graphicsControlMarker);
      offset !== -1;
      offset = asset.indexOf(graphicsControlMarker, offset + 8)
    ) {
      frameControls.push(offset + graphicsControlMarker.length);
    }

    it("matches the GIF to the GitHub theme", () => {
      expect(readme).toContain(`href="${file}#gh-${theme}-mode-only"`);
      expect(asset.subarray(0, 6).toString()).toBe("GIF89a");
    });

    it("lets the hidden GIF take no horizontal space", () => {
      // GitHub gives its GIF wrapper any width specified on the image.
      expect(readme).toContain(`<img src="${file}" alt="PrintStash logo" height="160" />`);
    });

    it("allows a transparent background on every frame", () => {
      expect(frameControls.length).toBeGreaterThan(0);
      expect(frameControls.every((offset) => asset[offset] & 1)).toBe(true);
    });

    it("plays one brief sequence", () => {
      expect(frameControls.length).toBeGreaterThan(1);
      const durationMs = frameControls.reduce(
        (sum, offset) => sum + asset.readUInt16LE(offset + 1) * 10,
        0,
      );
      expect(durationMs).toBeLessThanOrEqual(300);
      expect(asset.includes(Buffer.from("NETSCAPE2.0"))).toBe(false);
    });
  });
});
