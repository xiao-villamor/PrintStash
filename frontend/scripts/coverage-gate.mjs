#!/usr/bin/env node
/**
 * The frontend coverage gate: a floor per area of the tree, ratcheted both ways.
 *
 * Two things this replaces. First, `thresholds` in vite.config.ts, which can only
 * enforce a lower bound — and a floor nobody is ever forced to raise stops being a
 * gate, it becomes a number everything clears by twenty points. Every floor here is
 * two-sided: fall below it and the run fails; rise clear of it by more than the
 * slack and the run also fails, telling you to raise it.
 *
 * Second, and more importantly: that config measured `src/lib/**` and nothing else.
 * 1,639 of the app's 8,530 statements. It reported 86% and CI never ran it at all —
 * there was no coverage step in the frontend job. So the widened include here drops
 * the headline number from 86% to 36%, and none of that drop is a regression. It is
 * the 6,053 statements of `src/components/**` that were never in the picture.
 *
 * Those component numbers are low for a real reason as well as a bad one. The real
 * one: the route-level behaviour of this app is covered by Playwright — `tests/e2e/`
 * against a mock API and `tests/e2e-real/` against a live backend — and v8 coverage
 * of a vitest run cannot see a single line of it. So a low number on `src/pages/**`
 * does not mean the pages are untested; it means they are not tested *here*. The bad
 * reason is everything else, and the floors below are how that shrinks.
 *
 * Usage: `pnpm coverage` (measures, then runs this). Reads the `json-summary`
 * reporter's output, so a stale or missing report is an error, never a pass.
 */

import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = new URL("..", import.meta.url).pathname;

/**
 * `slack` is derived, not chosen: roughly three statements' worth of coverage for
 * the size of the thing being measured. Below that a single new test forces an edit
 * here for no benefit; above it a sustained improvement goes unrecorded.
 *
 * Set each floor to roughly `measured - slack/2`, not just under `measured`. v8
 * coverage of the same suite moves by a hundredth of a point between runs, and a
 * floor placed hard against the upper bound turns that jitter into a red CI run —
 * centring it leaves headroom on both sides.
 */
const slackFor = (totalStatements) => Math.max(0.5, 300 / Math.max(1, totalStatements));

/**
 * One entry per vitest run that writes a coverage report. `areas` are prefixes
 * relative to the suite's own directory; every measured file must fall under exactly
 * one of them, so a new top-level directory fails the gate until it is given a floor
 * rather than silently averaging into someone else's.
 */
const SUITES = [
  {
    name: "app",
    summary: "coverage/coverage-summary.json",
    command: "pnpm test:coverage",
    total: { statements: 80.7, branches: 73.2 },
    areas: [
      // The tested core: formatters, stores, query hooks, the api client. This is
      // the area where a unit test is the right tier, so it carries the real floor.
      { prefix: "src/lib/", statements: 92.2, branches: 81.5 },
      // The bulk of the app, and where the remaining debt is. Route-level
      // behaviour is covered by Playwright, which v8 cannot see; component-level
      // behaviour is being brought up module by module.
      { prefix: "src/components/", statements: 77.5, branches: 71.8 },
      // Pages are exercised end-to-end by tests/e2e/*.spec.ts, which this cannot
      // see. The floor records what vitest reaches, not what is tested.
      { prefix: "src/pages/", statements: 83.7, branches: 73.8 },
      // Root-level wiring: the router shell and the layout. Rendered by every
      // Playwright spec, unit-tested by nothing.
      { prefix: "src/", statements: 0, branches: 0 },
    ],
  },
  {
    name: "@printstash/domain",
    summary: "packages/domain/coverage/coverage-summary.json",
    command: "pnpm --filter @printstash/domain test:coverage",
    total: { statements: 94.5, branches: 91.7 },
    areas: [{ prefix: "src/", statements: 94.5, branches: 91.7 }],
  },
  {
    // The primitives DESIGN.md requires every component to compose. Being the
    // shared layer is exactly why its coverage matters more than its size suggests:
    // one uncovered branch in `overlay.ts` is an uncovered branch in every dialog.
    name: "@printstash/ui",
    summary: "packages/ui/coverage/coverage-summary.json",
    command: "pnpm --filter @printstash/ui test:coverage",
    total: { statements: 98.5, branches: 97.5 },
    areas: [{ prefix: "src/", statements: 98.5, branches: 97.5 }],
  },
];

const METRICS = ["statements", "branches"];
const failures = [];

/** Newest mtime under a directory tree, for the staleness check. */
const newestSource = (dir) => {
  let newest = 0;
  const walk = (path) => {
    for (const entry of readdirSync(path, { withFileTypes: true })) {
      if (entry.name === "node_modules" || entry.name === "coverage") continue;
      const child = join(path, entry.name);
      if (entry.isDirectory()) walk(child);
      else if (/\.(ts|tsx)$/.test(entry.name)) {
        newest = Math.max(newest, statSync(child).mtimeMs);
      }
    }
  };
  walk(dir);
  return newest;
};

const pct = (entry, metric) => entry[metric].pct;

for (const suite of SUITES) {
  const path = join(ROOT, suite.summary);
  if (!existsSync(path)) {
    failures.push(
      `${suite.name}: ${suite.summary} does not exist, so there is nothing to check ` +
        `coverage against. Run \`${suite.command}\` — or \`pnpm coverage\`, which ` +
        `runs every suite and then this gate.`,
    );
    continue;
  }

  const report = JSON.parse(readFileSync(path, "utf8"));
  const total = report.total;
  delete report.total;

  const files = Object.entries(report);
  if (files.length === 0) {
    failures.push(
      `${suite.name}: the report contains no files. An \`include\` that matches ` +
        `nothing reports 100% of zero, which passes every floor.`,
    );
    continue;
  }

  const totalStatements = total.statements.total;
  const slack = slackFor(totalStatements);

  for (const metric of METRICS) {
    const floor = suite.total[metric];
    const measured = pct(total, metric);
    if (measured < floor) {
      failures.push(
        `${suite.name} total ${metric}: ${measured.toFixed(2)}% < ${floor}% floor. ` +
          `Open the HTML report for the uncovered lines.`,
      );
    } else if (measured >= floor + slack) {
      failures.push(
        `${suite.name} total ${metric}: ${measured.toFixed(2)}% is clear of the ` +
          `${floor}% floor. Raise it to ${(measured - slack / 2).toFixed(1)} in ` +
          `scripts/coverage-gate.mjs so the gain cannot be given back.`,
      );
    }
  }

  // Areas are checked longest-prefix-first so `src/lib/` wins over `src/`.
  const areas = [...suite.areas].sort((a, b) => b.prefix.length - a.prefix.length);
  const buckets = new Map(
    areas.map((area) => [area.prefix, { statements: [0, 0], branches: [0, 0] }]),
  );
  const suiteDir = join(ROOT, suite.summary.replace(/coverage\/coverage-summary\.json$/, ""));

  for (const [absolute, entry] of files) {
    const relative = absolute.startsWith(suiteDir) ? absolute.slice(suiteDir.length) : absolute;
    const area = areas.find((candidate) => relative.startsWith(candidate.prefix));
    if (!area) {
      failures.push(
        `${suite.name}: ${relative} falls under no area in scripts/coverage-gate.mjs. ` +
          `Give it a floor — averaging it into a neighbouring area is how a new ` +
          `directory arrives untested and nobody notices.`,
      );
      continue;
    }
    const bucket = buckets.get(area.prefix);
    for (const metric of METRICS) {
      bucket[metric][0] += entry[metric].covered;
      bucket[metric][1] += entry[metric].total;
    }
  }

  for (const area of areas) {
    const bucket = buckets.get(area.prefix);
    const areaSlack = slackFor(bucket.statements[1]);
    for (const metric of METRICS) {
      const [covered, count] = bucket[metric];
      if (count === 0) continue;
      const measured = (100 * covered) / count;
      const floor = area[metric];
      if (measured < floor) {
        failures.push(
          `${suite.name} ${area.prefix} ${metric}: ${measured.toFixed(2)}% < ${floor}% floor.`,
        );
      } else if (measured >= floor + areaSlack) {
        failures.push(
          `${suite.name} ${area.prefix} ${metric}: ${measured.toFixed(2)}% is clear of ` +
            `the ${floor}% floor. Raise it to ${(measured - areaSlack / 2).toFixed(1)} ` +
            `in scripts/coverage-gate.mjs.`,
        );
      }
    }
  }

  const reportAge = statSync(path).mtimeMs;
  const sourceAge = newestSource(join(suiteDir, "src"));
  if (sourceAge > reportAge) {
    failures.push(
      `${suite.name}: a source file is newer than ${suite.summary}, so this gate ` +
        `would be judging a report that predates the change. Re-run \`pnpm coverage\`.`,
    );
  }

  const width = Math.max(...METRICS.map((metric) => metric.length));
  const line = METRICS.map(
    (metric) => `${metric.padEnd(width)} ${pct(total, metric).toFixed(2).padStart(6)}%`,
  ).join("   ");
  console.log(`${suite.name.padEnd(20)} ${line}`);
}

if (failures.length > 0) {
  console.error(`\ncoverage gate: ${failures.length} problem(s)\n`);
  for (const failure of failures) console.error(`  - ${failure}`);
  console.error("");
  process.exit(1);
}

console.log("\ncoverage gate: every floor held.");
