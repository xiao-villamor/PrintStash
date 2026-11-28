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

  it("uses semantic color tokens, not raw Tailwind palette colors", () => {
    expect(
      offenders(
        /\b(?:text|bg|border|ring|fill|stroke|divide|outline)-(?:red|green|amber|blue|emerald|yellow|orange|rose|sky|slate|gray|zinc|neutral|stone)-\d{2,3}\b/,
      ),
    ).toEqual([]);
  });

  it("does not force a menu radius that the floating primitive owns", () => {
    expect(offenders(/contentClassName="[^"]*\brounded(?:-md)?(?=[\s"])/)).toEqual([]);
  });

  it("never draws a bordered box with square corners", () => {
    // A class string that borders a box (`border` + a border colour) must also
    // round it: panels 8px (rounded-lg), controls 6px (rounded-md). Popover
    // content is rounded by the floating primitive and is exempt.
    const colour =
      /^border-(?:border|input|border-strong|dashed|dotted|outline-variant|destructive|warning|success|primary)(?:\/\d+)?$/;
    const square: string[] = [];
    for (const file of sourceFiles(root)) {
      const text = fs.readFileSync(file, "utf8");
      for (const match of text.matchAll(/(["`])((?:(?!\1)[^\n])*?)\1/g)) {
        const tokens = match[2].split(/\s+/);
        if (!tokens.includes("border")) continue;
        if (!tokens.some((token) => colour.test(token))) continue;
        if (tokens.some((token) => token.startsWith("rounded") || token.startsWith("[&"))) continue;
        if (tokens.includes("bg-popover") || tokens.includes("border-0")) continue;
        square.push(`${path.relative(root, file)}: ${match[2].slice(0, 80)}`);
      }
    }
    expect(square).toEqual([]);
  });

  it("keeps the Card primitive rounded", () => {
    const card = fs.readFileSync(
      path.resolve(root, "../packages/ui/src/components/card.tsx"),
      "utf8",
    );
    expect(card).toContain("rounded-lg");
    expect(card).not.toContain("rounded-none");
  });

  it("keeps hover visibly different from selection", () => {
    const globals = fs.readFileSync(path.resolve(root, "globals.css"), "utf8");
    expect(offenders(/hover:bg-accent\b/)).toEqual([]);
    const accent = /--accent: ([^;]+);/.exec(globals)?.[1];
    const muted = /--muted: ([^;]+);/.exec(globals)?.[1];
    expect(accent).not.toBe(muted);
  });

  it("rounds every CSS rule that draws a 1px border box", () => {
    // Tag chips take their radius from a later, more specific rule.
    const exempt = new Set([
      ".collection-header-tag",
      ".collection-header-tags .collection-add-tag",
      ".collection-header-tag-input",
    ]);
    const square: string[] = [];
    const walk = (dir: string): string[] =>
      fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return entry.name === "__tests__" ? [] : walk(full);
        return entry.name.endsWith(".css") ? [full] : [];
      });
    for (const file of walk(root)) {
      const css = fs.readFileSync(file, "utf8");
      for (const rule of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
        const selector = rule[1].trim().replace(/\s+/g, " ");
        if (exempt.has(selector)) continue;
        if (
          /(?:^|[;\n])\s*border:\s*1px\s+(?:solid|dashed|dotted)/.test(rule[2]) &&
          !rule[2].includes("radius")
        ) {
          square.push(`${path.relative(root, file)}: ${selector}`);
        }
      }
    }
    expect(square).toEqual([]);
  });

  it("keeps one baseline focus indicator for elements without their own ring", () => {
    const globals = fs.readFileSync(path.resolve(root, "globals.css"), "utf8");
    expect(globals).toMatch(/:focus-visible\s*\{[^}]*outline:\s*2px solid var\(--ring\)/);
  });

  it("lightens primary-coloured text on dark surfaces", () => {
    const globals = fs.readFileSync(path.resolve(root, "globals.css"), "utf8");
    expect(globals).toMatch(/\.dark \.text-primary\s*\{[^}]*color:\s*#7896bc/);
  });
});
