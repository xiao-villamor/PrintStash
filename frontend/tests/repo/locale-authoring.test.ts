/** Static locale authoring stays deterministic, complete, and safe to rerun. */
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { scaffoldLocale } from "../../scripts/scaffold-locale.mjs";

const roots: string[] = [];

function fixture(): string {
  const root = mkdtempSync(join(tmpdir(), "printstash-locale-"));
  roots.push(root);
  mkdirSync(join(root, "src/locales"), { recursive: true });
  writeFileSync(
    join(root, "src/locales/en.json"),
    `${JSON.stringify({ greeting: "Hello {name}", files: { one: "{count} file", other: "{count} files" } }, null, 2)}\n`,
  );
  writeFileSync(
    join(root, "src/locales/catalogs.ts"),
    'import en from "./en.json" with { type: "json" };\n\nexport const localeDefinitions = {\n  en: { name: "English", direction: "ltr", messages: en },\n} satisfies Record<string, unknown>;\n',
  );
  return root;
}

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

describe("locale authoring", () => {
  it("creates a complete statically registered hardcoded draft", () => {
    const root = fixture();

    const result = scaffoldLocale({
      rootDir: root,
      locale: "fr",
      name: "Français",
      direction: "ltr",
    });

    expect(result.locale).toBe("fr");
    expect(JSON.parse(readFileSync(join(root, "src/locales/fr.json"), "utf8"))).toEqual({
      greeting: "TODO(fr): Hello {name}",
      files: { one: "TODO(fr): {count} file", other: "TODO(fr): {count} files" },
    });
    expect(readFileSync(join(root, "src/locales/catalogs.ts"), "utf8")).toContain(
      'import catalogFr from "./fr.json" with { type: "json" };',
    );
    expect(readFileSync(join(root, "src/locales/catalogs.ts"), "utf8")).toContain(
      'fr: { name: "Français", direction: "ltr", messages: catalogFr },',
    );
  });

  it("canonicalizes a BCP 47 language tag", () => {
    const root = fixture();

    expect(
      scaffoldLocale({ rootDir: root, locale: "pt-br", name: "Português", direction: "ltr" })
        .locale,
    ).toBe("pt-BR");
    expect(readFileSync(join(root, "src/locales/pt-BR.json"), "utf8")).toContain(
      '"TODO(pt-BR): Hello {name}"',
    );
  });

  it.each([
    ["an invalid tag", "ltr"],
    ["fr", "sideways"],
  ])("rejects locale input %s / %s", (locale, direction) => {
    expect(() =>
      scaffoldLocale({ rootDir: fixture(), locale, name: "Example", direction }),
    ).toThrow(/Invalid locale tag|Direction must/);
  });

  it("refuses to overwrite an existing locale", () => {
    const root = fixture();
    scaffoldLocale({ rootDir: root, locale: "fr", name: "Français", direction: "ltr" });

    expect(() =>
      scaffoldLocale({ rootDir: root, locale: "fr", name: "Français", direction: "ltr" }),
    ).toThrow(/already exists/);
  });
});
