/*
 * The package barrel is the whole of `@printstash/ui`'s public surface: the web app
 * and the browser extension import from `@printstash/ui`, never from a file path
 * inside it.
 *
 * That makes an unexported module invisible — a new primitive lands, nothing fails,
 * and the app either re-implements it or reaches into `@printstash/ui/components/...`
 * and pins itself to this package's internal layout. Neither shows up as a broken
 * test anywhere else, so the barrel's completeness is asserted here against the
 * filesystem rather than against a hand-maintained list that would drift the same way.
 */

import { describe, expect, it } from "vitest";

import * as publicApi from "../index";

/** Every component and lib module, keyed by path — the set the barrel must cover. */
const modules = import.meta.glob<object>("../{components,lib}/*.{ts,tsx}", { eager: true });

/** Each module's own named exports — the default export is not part of the barrel. */
const sourceModules = Object.entries(modules)
  .filter(([path]) => !path.includes("__tests__"))
  .map(([path, module]) => ({
    path,
    exports: Object.keys(module).filter((name) => name !== "default"),
  }));

describe("@printstash/ui barrel", () => {
  it("re-exports every named export of every module in the package", () => {
    const missing = sourceModules.flatMap(({ path, exports }) =>
      exports.filter((name) => !(name in publicApi)).map((name) => `${path}#${name}`),
    );

    expect(missing).toEqual([]);
  });

  it("exports nothing the modules do not define", () => {
    const declared = new Set(sourceModules.flatMap(({ exports }) => exports));

    expect(Object.keys(publicApi).filter((name) => !declared.has(name))).toEqual([]);
  });
});
