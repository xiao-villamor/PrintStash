/** Keeps extension settings and status out of native disclosures. */
import { readdir, readFile } from "node:fs/promises";
import { describe, expect, it } from "vitest";

const disclosureMarkup = /<\/?(?:details|summary)\b/i;
const disclosureCreation =
  /\.createElement(?:NS)?\(\s*(?:(["'`])[^"'`]+\1\s*,\s*)?(["'`])(?:details|summary)\2/i;

describe("extension entrypoints", () => {
  it("ships HTML entrypoints without native disclosures", async () => {
    const files = (await readdir("entrypoints", { recursive: true })).filter((path) =>
      path.endsWith(".html"),
    );

    const markup = await Promise.all(files.map((path) => readFile(`entrypoints/${path}`, "utf8")));

    expect(files.length).toBeGreaterThan(0);
    expect(files.filter((_, index) => disclosureMarkup.test(markup[index]))).toEqual([]);
  });

  it("creates no native disclosures in the popup", async () => {
    const source = await readFile("popup.ts", "utf8");

    expect(source).not.toMatch(disclosureMarkup);
    expect(source).not.toMatch(disclosureCreation);
  });
});
