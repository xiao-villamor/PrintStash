import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";

import ts from "typescript";
import { expect, it } from "vitest";

import { hasUiTranslation } from "@/components/ui/localized";

const NON_TRANSLATABLE_LITERALS = new Set([
  ".gcode, .g, or .gco", "· v", "· Z", "*/30 * * * * (min hour dom mon dow)", "/api/v1/auth/login",
  "&quot;", "&rdquo;?", "# Document&#10;&#10;Write markdown. Paste or drop images to embed them.", "°C", "→ recycle bin",
  "3D", "Authentik", "auto", "Create &quot;", "Delete &ldquo;", "GCode", "GCODE", "GitHub", "Klipper", "mm",
  "Moonraker", "my-backup-bucket", "my-vault-bucket", "PLA", "PLA, PETG…", "printstash", "PrintStash", "PrintStash ·",
  "PrintStash v", "Select all on screen (", "Spoolman", "token", "Voron 2.4", "Voron 2.4 — 0.4 mm", "you@example.com",
  "(Printables / MakerWorld), or a direct file/.zip link — fetched on the server.",
  "Fetch &amp; Import",
  "Fetch recent print history from a Moonraker printer and import jobs matching this model&apos;s G-code files.",
  "Moonraker&apos;s native Spoolman integration is already decrementing the active spool, so PrintStash automatically skips its own write-back to avoid double-counting. Only override this if you have disabled Moonraker&apos;s hook and want PrintStash to count consumption.",
  "Write back anyway (I disabled Moonraker&apos;s hook)",
  "http://spoolman.local:7912", "https://&lt;id&gt;.r2.cloudflarestorage.com", "https://<id>.r2.cloudflarestorage.com",
  "https://auth.example.com/application/o/printstash", "https://printables.com/model/...", "https://www.printables.com/model/...",
]);

const TRANSLATABLE_ATTRIBUTES = new Set(["title", "placeholder", "aria-label", "ariaLabel", "description", "label", "confirmLabel", "hint"]);

function uiLiterals(file: string): string[] {
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const values = new Set<string>();
  const add = (value: string) => {
    const normalized = value.replace(/\s+/g, " ").trim();
    if (normalized.length > 1 && /[A-Za-z]/.test(normalized)) values.add(normalized);
  };
  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) add(node.getText(source));
    if (ts.isJsxAttribute(node) && ts.isIdentifier(node.name) && TRANSLATABLE_ATTRIBUTES.has(node.name.text) && node.initializer && ts.isStringLiteral(node.initializer)) add(node.initializer.text);
    ts.forEachChild(node, visit);
  };
  visit(source);
  return [...values];
}

function sourceFiles(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.tsx?$/.test(entry.name) ? [path] : [];
  });
}

it("covers every translatable JSX literal with a Spanish catalog entry", () => {
  const files = sourceFiles("src")
    .filter((file) => !file.includes("/__tests__/") && !file.endsWith("localized.tsx") && !file.endsWith("i18n.tsx"));
  const missing = files.flatMap((file) => uiLiterals(file)
    .filter((value) => !NON_TRANSLATABLE_LITERALS.has(value) && !hasUiTranslation("es", value))
    .map((value) => `${file}: ${value}`));

  expect(missing).toEqual([]);
});
