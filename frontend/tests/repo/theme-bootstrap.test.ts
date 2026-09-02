/*
 * The saved theme is restored before React starts under the production CSP.
 *
 * nginx deliberately rejects inline scripts. Keeping the pre-paint bootstrap
 * external prevents a refresh from silently dropping the saved preference and
 * returning the UI to light mode.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { runInNewContext } from "node:vm";

import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "../..");
const bootstrap = readFileSync(resolve(root, "public/theme-bootstrap.js"), "utf8");

function runBootstrap(savedTheme: string | null, prefersDark: boolean) {
  let dark = false;
  const favicon = { href: "", id: "", rel: "", type: "" };

  runInNewContext(bootstrap, {
    document: {
      documentElement: {
        classList: {
          toggle(_className: string, enabled: boolean) {
            dark = enabled;
          },
        },
      },
      createElement() {
        return favicon;
      },
      head: {
        appendChild() {},
      },
    },
    localStorage: {
      getItem(key: string) {
        return key === "printstash.theme" ? savedTheme : null;
      },
    },
    window: {
      matchMedia() {
        return { matches: prefersDark };
      },
    },
  });

  return { dark, faviconHref: favicon.href };
}

describe("theme bootstrap", () => {
  it("restores a saved dark preference", () => {
    expect(runBootstrap("dark", false)).toEqual({
      dark: true,
      faviconHref: "/icon-dark.svg?v=2",
    });
  });

  it("honours a saved light preference over a dark OS preference", () => {
    expect(runBootstrap("light", true)).toEqual({
      dark: false,
      faviconHref: "/icon-light.svg?v=2",
    });
  });

  it("loads externally before the React entrypoint", () => {
    const html = readFileSync(resolve(root, "index.html"), "utf8");
    const bootstrapTag = '<script src="/theme-bootstrap.js"></script>';
    const appTag = '<script type="module" src="/src/main.tsx"></script>';

    expect(html).toContain(bootstrapTag);
    expect(html.indexOf(bootstrapTag)).toBeLessThan(html.indexOf(appTag));
    expect(html).not.toContain('localStorage.getItem("printstash.theme")');
  });
});
