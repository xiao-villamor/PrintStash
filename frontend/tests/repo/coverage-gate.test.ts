/** Coverage floors reject regressions while reporting gains for maintenance. */
import { spawnSync } from "node:child_process";
import { copyFileSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { afterEach, describe, expect, test } from "vitest";

const roots: string[] = [];

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

function runGate(appPercent: number) {
  const root = mkdtempSync(join(tmpdir(), "printstash-coverage-gate-"));
  roots.push(root);
  mkdirSync(join(root, "scripts"));
  copyFileSync(resolve("scripts/coverage-gate.mjs"), join(root, "scripts/coverage-gate.mjs"));

  const suites = [
    ["", appPercent],
    ["packages/domain/", 100],
    ["packages/ui/", 100],
  ] as const;
  for (const [prefix, percent] of suites) {
    const suiteRoot = join(root, prefix);
    const source = join(suiteRoot, "src/example.ts");
    const reportPath = join(suiteRoot, "coverage/coverage-summary.json");
    mkdirSync(join(suiteRoot, "src"), { recursive: true });
    mkdirSync(join(suiteRoot, "coverage"), { recursive: true });
    writeFileSync(source, "export const example = true;\n");
    const covered = percent;
    const metric = { total: 100, covered, skipped: 0, pct: percent };
    writeFileSync(
      reportPath,
      JSON.stringify({
        total: { statements: metric, branches: metric },
        [source]: { statements: metric, branches: metric },
      }),
    );
  }

  const result = spawnSync(process.execPath, [join(root, "scripts/coverage-gate.mjs")], {
    encoding: "utf8",
  });
  return { status: result.status, output: result.stdout + result.stderr };
}

describe("coverage floors", () => {
  test("coverage improvement stays green", () => {
    const result = runGate(100);
    expect(result.status).toBe(0);
    expect(result.output).toContain("coverage improvements to review during maintenance");
  });

  test("coverage regression fails", () => {
    const result = runGate(80);
    expect(result.status).toBe(1);
    expect(result.output).toContain("app total statements: 80.00% < 83.5% floor");
  });
});
