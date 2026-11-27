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
    return /\.tsx$/.test(entry.name) ? [full] : [];
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
});
