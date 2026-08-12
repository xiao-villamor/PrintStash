import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import { APP_VERSION, CHANGELOG } from "@/lib/changelog";

/**
 * The Settings → About "Latest changes" card renders CHANGELOG[0]. It is the
 * one place the user-facing version + release notes live, and it is easy to
 * forget when cutting a release. These guards turn that drift into a failing
 * test rather than a stale About tab in production.
 */

// vitest runs from the frontend package root, so package.json sits at cwd.
const pkg = JSON.parse(
  readFileSync(join(process.cwd(), "package.json"), "utf8"),
) as { version: string };

// Version bumps are a triple (backend/pyproject.toml, config.py's
// app_version, frontend/package.json) — this only guards the frontend/backend
// half, but that's the half that silently drifted before (0.8.5 addenda #2).
const backendPyproject = readFileSync(
  join(process.cwd(), "..", "backend", "pyproject.toml"),
  "utf8",
);
const backendVersionMatch = backendPyproject.match(/^version = "([^"]+)"/m);
if (!backendVersionMatch) {
  throw new Error("could not find version in backend/pyproject.toml");
}
const backendVersion = backendVersionMatch[1];
const backendConfig = readFileSync(
  join(process.cwd(), "..", "backend", "app", "core", "config.py"),
  "utf8",
);
const backendConfigVersionMatch = backendConfig.match(
  /^\s*app_version: str = "([^"]+)"/m,
);
if (!backendConfigVersionMatch) {
  throw new Error("could not find app_version in backend/app/core/config.py");
}
const backendConfigVersion = backendConfigVersionMatch[1];

describe("changelog ↔ package.json", () => {
  it("APP_VERSION is the newest changelog entry", () => {
    expect(APP_VERSION).toBe(CHANGELOG[0].version);
  });

  it("the newest changelog entry matches the shipped app version", () => {
    // Bumping package.json without adding the matching changelog entry (or vice
    // versa) breaks here — keep them in lockstep on every release.
    expect(CHANGELOG[0].version).toBe(pkg.version);
  });

  it("frontend package.json matches backend/pyproject.toml", () => {
    expect(pkg.version).toBe(backendVersion);
  });

  it("the backend runtime version matches the package versions", () => {
    expect(backendConfigVersion).toBe(pkg.version);
  });
});

describe("changelog integrity", () => {
  it("every entry is well-formed and has at least one change", () => {
    for (const entry of CHANGELOG) {
      expect(entry.version).toMatch(/^\d+\.\d+\.\d+$/);
      expect(entry.date.length).toBeGreaterThan(0);
      expect(entry.changes.length).toBeGreaterThan(0);
      expect(entry.changes.every((c) => c.trim().length > 0)).toBe(true);
    }
  });

  it("versions are unique and sorted newest-first", () => {
    const versions = CHANGELOG.map((e) => e.version);
    expect(new Set(versions).size).toBe(versions.length);

    const toTuple = (v: string) => v.split(".").map(Number);
    const sorted = [...versions].sort((a, b) => {
      const [aMaj, aMin, aPatch] = toTuple(a);
      const [bMaj, bMin, bPatch] = toTuple(b);
      return bMaj - aMaj || bMin - aMin || bPatch - aPatch;
    });
    expect(versions).toEqual(sorted);
  });
});
