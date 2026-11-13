/** Test entry points must discover real tests instead of silently dropping suites. */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import fast from "../../vitest.fast.config";
const root = resolve(__dirname, "../..");
const members = fast.test?.include ?? [];
describe("test entry points", () => {
  it("discovers every declared fast-lane member", () => {
    expect(members.length).toBeGreaterThan(0);
    expect(members.filter((file) => !existsSync(resolve(root, file)))).toEqual([]);
  });
  it("keeps the shared-worker lane free of mutable global fixtures", () => {
    const forbidden =
      /vi\.(?:stubGlobal|mock|spyOn|useFakeTimers)|document\.|window\.|localStorage|globalThis/;
    const unsafe = members.filter(
      (file) =>
        existsSync(resolve(root, file)) &&
        forbidden.test(readFileSync(resolve(root, file), "utf8")),
    );
    expect(unsafe).toEqual([]);
  });
  it("exposes an execution entry point for every browser configuration", () => {
    const packageJson = JSON.parse(readFileSync(resolve(root, "package.json"), "utf8"));
    const workflows = resolve(root, "../.github/workflows");
    const commands = [
      JSON.stringify(packageJson.scripts),
      ...readdirSync(workflows)
        .filter((file) => /\.ya?ml$/.test(file))
        .map((file) => readFileSync(resolve(workflows, file), "utf8")),
    ].join("\n");
    const configs = readdirSync(root).filter(
      (file) => /^playwright\..*config\.ts$/.test(file) && file !== "playwright.config.ts",
    );
    expect(configs.filter((file) => !commands.includes(file))).toEqual([]);
  });
  it("pins Playwright installation jobs to a supported runner", () => {
    const workflows = resolve(root, "../.github/workflows");
    const jobs = readdirSync(workflows)
      .filter((file) => /\.ya?ml$/.test(file))
      .flatMap((file) => {
        const source = readFileSync(resolve(workflows, file), "utf8");
        const section = source.split(/^jobs:[ \t]*$/m)[1] ?? "";
        const definitions = [...section.matchAll(/^  ([\w-]+):[ \t]*$/gm)];
        return definitions.flatMap((definition, index) => {
          const body = section.slice(definition.index, definitions[index + 1]?.index);
          return body.includes("pnpm exec playwright install --with-deps chromium")
            ? [{ name: `${file}:${definition[1]}`, body }]
            : [];
        });
      });

    expect(jobs.map((job) => job.name).sort()).toEqual([
      "ci.yml:browser-smoke",
      "deep-ci.yml:browser-mock",
      "deep-ci.yml:browser-real",
      "deep-ci.yml:library-startup",
      "tooling-experiments.yml:frontend",
    ]);
    expect(jobs.filter((job) => !/^    runs-on: ubuntu-24\.04[ \t]*$/m.test(job.body))).toEqual([]);
  });
});
