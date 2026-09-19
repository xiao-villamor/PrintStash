/** Production locale compaction must reduce bytes without changing any translated copy. */
import { gzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { compactCatalogs } from "../../scripts/compact-catalogs";
import { localeDefinitions, type Message } from "@/locales/catalogs";

const catalogs = Object.fromEntries(
  Object.entries(localeDefinitions).map(([locale, definition]) => [locale, definition.messages]),
);
const inconsistentCatalogs: { name: string; messages: Record<string, Message> }[] = [
  { name: "missing", messages: {} },
  { name: "extra", messages: { Files: "Archivos", Extra: "Extra" } },
  { name: "renamed", messages: { Other: "Otro" } },
];

describe("compactCatalogs", () => {
  it.each(Object.keys(catalogs))("preserves every registered catalog: %s", (locale) => {
    const { keys, values } = compactCatalogs(catalogs);

    const decoded = Object.fromEntries(keys.map((key, i) => [key, values[locale][i] ?? key]));

    expect(decoded).toEqual(catalogs[locale]);
  });

  it("reduces compressed catalog payload", () => {
    const original = gzipSync(JSON.stringify(catalogs));

    const compact = gzipSync(JSON.stringify(compactCatalogs(catalogs)));

    expect(compact.byteLength).toBeLessThan(original.byteLength * 0.75);
  });

  it("preserves special message values", () => {
    const messages = {
      count: { one: "One file", other: "{count} files" },
      empty: "",
      Files: "Files",
    };

    const { keys, values } = compactCatalogs({ en: messages });
    const decoded = Object.fromEntries(keys.map((key, i) => [key, values.en[i] ?? key]));

    expect(decoded).toEqual(messages);
  });

  it.each(inconsistentCatalogs)("rejects inconsistent catalog keys: $name", ({ messages }) => {
    expect(() => compactCatalogs({ en: { Files: "Files" }, es: messages })).toThrow(
      "Locale es must contain exactly the English message keys.",
    );
  });
});
