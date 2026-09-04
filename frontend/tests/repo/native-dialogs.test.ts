/*
 * No `window.prompt`, `alert` or `confirm` anywhere in the app.
 *
 * This is a design rule with teeth: native dialogs cannot be styled, cannot be
 * localized, block the whole tab, and are suppressed outright in some contexts —
 * so a `confirm()` guarding a destructive action can silently return false and
 * make the button appear broken, or silently return true. `DESIGN.md` bans them
 * and this is what enforces it, by reading the source rather than by hoping.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

function sourceFiles(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(root, entry.name);
    return entry.isDirectory() ? sourceFiles(full) : /\.(ts|tsx)$/.test(entry.name) ? [full] : [];
  });
}

describe("nativeDialogUsage", () => {
  it("never uses browser-native prompt, alert, or confirm dialogs", () => {
    const root = path.resolve(__dirname, "../../src");
    const findings = sourceFiles(root).flatMap((file) => {
      const source = fs.readFileSync(file, "utf8");
      return /window\.(prompt|alert|confirm)\s*\(/.test(source) ? [path.relative(root, file)] : [];
    });
    expect(findings).toEqual([]);
  });
});
