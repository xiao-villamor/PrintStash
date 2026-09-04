/*
 * The shape rules for this repo's own tests, checked by reading them.
 *
 * These are the frontend half of `backend/tests/repo/test_test_hygiene.py`, and
 * they exist for the same reason: a test suite decays in ways that never fail.
 * A file with no header is readable today and unmaintainable in a year, when it
 * goes red and the obvious fix is to delete the assertion nobody can justify. A
 * test outside a `describe` belongs to nothing, so it accumulates in write order
 * and drifts away from the component it defends. Neither is a bug, so nothing
 * else would ever report them.
 *
 * The mirror rule is the one with the most leverage. A test file named for a
 * *concept* — `send-to-queue`, `inbox-navigation`, `small-clients` — cannot be
 * found from the module it defends, so "is this module tested?" stops being one
 * `ls` and becomes a search. That is what makes a coverage matrix guesswork:
 * nobody can tell an untested module from one whose tests live under a name they
 * did not think of. So a test file's name is its module's name, and its home is
 * the `__tests__` directory beside it.
 *
 * These three rules are absolute rather than ratcheted: the whole suite was
 * converted, so the first violation to appear is the regression, and it appears
 * in the PR that wrote it.
 *
 * The conjunction rule is capped rather than absolute, because a mechanical split
 * of a name that legitimately needs "and" produces duplicated setup in two tests
 * that assert halves of one behaviour. The cap may only fall.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const FRONTEND_ROOT = path.resolve(__dirname, "../..");

/**
 * Every tree holding vitest suites: the app, the workspace packages, and this
 * directory. `tests/e2e*` and `tests/performance` are Playwright's and are swept
 * too — the header and describe rules apply to them just as much.
 */
const SUITE_ROOTS = ["src", "tests", "packages"];

/**
 * Test names containing " and ". Some hold two behaviours, which a failure cannot
 * tell apart; others describe one invariant that needs the word. Both are worth
 * reducing and neither is worth a mechanical split, so the count is capped.
 *
 * The cap held at 133 when the sweep widened from `src` + `tests` to include
 * `packages/`, so the same number now covers strictly more files.
 */
const MAX_CONJUNCTION_NAMES = 129;

/**
 * Files that defend the repository rather than a module, so no mirror exists to
 * name them after. This is the frontend's `backend/tests/repo/`; anything outside
 * it must mirror.
 */
const REPO_INVARIANT_DIR = "tests/repo/";

/** Playwright specs are named for a user-facing flow, not for a module. */
const FLOW_SPEC_DIRS = ["tests/e2e/", "tests/e2e-real/", "tests/performance/"];

function testFiles(root: string): string[] {
  if (!fs.existsSync(root)) return [];
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) return [];
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) return testFiles(full);
    return /\.(test|spec)\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

function suiteFiles(): { file: string; relative: string; source: string }[] {
  return SUITE_ROOTS.flatMap((root) => testFiles(path.join(FRONTEND_ROOT, root))).map((file) => ({
    file,
    relative: path.relative(FRONTEND_ROOT, file),
    source: fs.readFileSync(file, "utf8"),
  }));
}

/** The production module a test file mirrors, or null when nothing matches. */
function mirrorOf(file: string): string | null {
  const directory = path.dirname(file);
  const basename = path.basename(file).replace(/\.(test|spec)\.tsx?$/, "");
  // `__tests__/foo.test.ts` mirrors `../foo.ts`; a file one level deeper, in
  // `__tests__/foo/bar.test.ts`, mirrors the module the *directory* is named for,
  // which is how a module too large for one test file is split.
  const candidates = [
    path.join(directory, "..", `${basename}.ts`),
    path.join(directory, "..", `${basename}.tsx`),
    path.join(directory, "..", basename, "index.ts"),
    path.join(directory, "..", basename, "index.tsx"),
    path.join(directory, "..", "..", `${path.basename(directory)}.ts`),
    path.join(directory, "..", "..", `${path.basename(directory)}.tsx`),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? null;
}

describe("suiteHygiene", () => {
  it("opens every test file with a contract header", () => {
    const offenders = suiteFiles()
      .filter(({ source }) => !/^\s*(\/\*|\/\/)/.test(source.split("\n")[0] ?? ""))
      .map(({ relative }) => relative);

    expect(offenders, "start each file with a block comment saying what it defends").toEqual([]);
  });

  it("defines every test inside a describe", () => {
    const offenders = suiteFiles().flatMap(({ relative, source }) => {
      const top = source
        .split("\n")
        .filter((line) =>
          /^(?:it|test)(?:\.(?:only|skip|fixme|failing|concurrent|sequential))?\(/.test(line),
        ).length;
      return top ? [`${relative} (${top})`] : [];
    });

    expect(offenders, "wrap each test in a describe naming the unit it exercises").toEqual([]);
  });

  it("names every unit test after the module it defends", () => {
    const offenders = suiteFiles()
      .filter(({ relative }) => !relative.startsWith(REPO_INVARIANT_DIR))
      .filter(({ relative }) => !FLOW_SPEC_DIRS.some((dir) => relative.startsWith(dir)))
      .filter(({ file }) => mirrorOf(file) === null)
      .map(({ relative }) => relative);

    expect(
      offenders,
      "name each test after its module and put it in the __tests__ beside it",
    ).toEqual([]);
  });

  it("keeps the count of test names joining two behaviours falling", () => {
    const offenders = suiteFiles().flatMap(({ relative, source }) =>
      [...source.matchAll(/\b(?:it|test)\(\s*["'`]([^"'`]*\sand\s[^"'`]*)["'`]/g)].map(
        (match) => `${relative}::${match[1]}`,
      ),
    );

    expect(offenders.length).toBeLessThanOrEqual(MAX_CONJUNCTION_NAMES);
    // Lower the cap whenever it falls, or it stops meaning anything.
    expect(offenders.length).toBe(MAX_CONJUNCTION_NAMES);
  });
});
