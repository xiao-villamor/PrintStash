import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "../../..");

describe("theme-aware favicon", () => {
  it("uses current blue brand in dark mode and versioned asset URLs", () => {
    const darkIcon = readFileSync(resolve(root, "public/icon-dark.svg"), "utf8");
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    const toggle = readFileSync(
      resolve(root, "src/components/theme-toggle.tsx"),
      "utf8",
    );

    expect(darkIcon).toContain("#2767FF");
    expect(darkIcon).toContain("#0E48F0");
    expect(darkIcon).not.toMatch(/#fb923c|#ea580c/i);
    expect(html).toContain('/icon-dark.svg?v=2');
    expect(toggle).toContain('/icon-dark.svg?v=2');
  });
});
