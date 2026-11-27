/*
 * UI consistency guards from the 2026-11 UI/UX audit. Each rule is a
 * DESIGN.md rule that drifted; reading the source keeps it from drifting again.
 */

import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const root = path.resolve(__dirname, "../../src");

function sourceFiles(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === "__tests__" ? [] : sourceFiles(full);
    return entry.name.endsWith(".tsx") ? [full] : [];
  });
}

function offenders(pattern: RegExp): string[] {
  return sourceFiles(root).flatMap((file) => {
    const lines = fs.readFileSync(file, "utf8").split("\n");
    return lines.flatMap((line, i) =>
      pattern.test(line) ? [`${path.relative(root, file)}:${i + 1}`] : [],
    );
  });
}

describe("design consistency", () => {
  it("has no <details>/<summary> disclosure triangles", () => {
    expect(offenders(/<(details|summary)\b/)).toEqual([]);
  });

  it("uses the text-field token, not text-[13px]", () => {
    expect(offenders(/text-\[13px\]/)).toEqual([]);
  });

  it("uses named z-index layers or local stacking (z-10/z-20), not ad hoc values", () => {
    expect(offenders(/\bz-(30|40|50|60|\[\d+\])\b/)).toEqual([]);
  });

  it("does not hand-size chrome with arbitrary pixel heights", () => {
    expect(offenders(/\bh-\[(30|34|38|57)px\]/)).toEqual([]);
  });
});
