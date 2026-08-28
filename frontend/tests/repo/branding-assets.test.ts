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

const root = resolve(import.meta.dirname, "../..");

describe("faviconFor", () => {
  it("uses current blue brand in dark mode and versioned asset URLs", () => {
    const darkIcon = readFileSync(resolve(root, "public/icon-dark.svg"), "utf8");
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    const toggle = readFileSync(resolve(root, "src/components/theme-toggle.tsx"), "utf8");

    expect(darkIcon).toContain("#2767FF");
    expect(darkIcon).toContain("#0E48F0");
    expect(darkIcon).not.toMatch(/#fb923c|#ea580c/i);
    expect(html).toContain("/icon-dark.svg?v=2");
    expect(toggle).toContain("/icon-dark.svg?v=2");
  });
});
