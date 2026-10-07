/** Enforce migrated ownership through resolved dependencies, including type and lazy edges. */
import { resolve, join } from "node:path";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { afterEach, describe, expect, it } from "vitest";
import {
  inspectDependencies,
  readDependencyProject,
  MIGRATION_EXCEPTIONS,
  type DependencyProject,
} from "../../scripts/dependency-boundaries";

function project(files: Record<string, string>): DependencyProject {
  return {
    files: new Map(Object.entries(files)),
    aliases: { "@/*": ["./src/*"] },
    packages: [
      {
        name: "@printstash/ui",
        directory: "packages/ui",
        exports: { ".": "./src/index.ts", "./*": "./src/components/*.tsx" },
      },
      {
        name: "@printstash/domain",
        directory: "packages/domain",
        exports: { ".": "./src/index.ts", "./format": "./src/format.ts" },
      },
    ],
  };
}
function inspect(files: Record<string, string>) {
  return inspectDependencies(project(files), []);
}
function edge(from: string, target: string, source: string) {
  return inspect({ [from]: source, [target]: "export const value = 1;" });
}

const workspaceRoots: string[] = [];
function workspaceFixture(packages: string[], declaration: string) {
  const root = mkdtempSync(join(tmpdir(), "printstash-dependencies-"));
  workspaceRoots.push(root);
  writeFileSync(join(root, "pnpm-workspace.yaml"), declaration);
  for (const name of packages) {
    mkdirSync(join(root, "packages", name), { recursive: true });
    writeFileSync(join(root, "packages", name, "package.json"), JSON.stringify({ name }));
  }
  return root;
}

describe("inspectDependencies", () => {
  it("accepts permitted dependency directions", () => {
    const result = inspect({
      "src/pages/setup.tsx": 'import { setupEntryOptions } from "@/features/setup/entry";',
      "src/features/setup/entry.ts": 'import { getSetupStatus } from "@/lib/api/config";',
      "src/lib/api/config.ts": 'import { getJson } from "./request";',
      "src/lib/api/request.ts": 'import { getStoredToken } from "@/lib/auth";',
      "src/lib/auth.ts": 'export { getStoredToken } from "./auth-store";',
      "src/lib/auth-store.ts": "export const getStoredToken = () => null;",
      "src/lib/queries.ts": 'import { options } from "@/features/library/multipart";',
      "src/features/library/multipart.ts": "export const options = {};",
      "packages/ui/src/index.ts": 'import { format } from "@printstash/domain/format";',
      "packages/domain/src/format.ts": "export const format = String;",
    });

    expect(result.diagnostics).toEqual([]);
  });

  it.each([
    "src/features/printers/settings-edit.ts",
    "src/features/printers/settings-review.tsx",
    "src/features/library/builds.ts",
  ])("accepts published editing and Build interfaces: %s", (target) => {
    const result = edge(
      "src/components/consumer.tsx",
      target,
      `import { value } from "@/${target.slice(4).replace(/\.tsx?$/, "")}";`,
    );

    expect(result.diagnostics).toEqual([]);
  });

  it.each([
    { label: "relative", specifier: "./leaf", target: "src/leaf.ts" },
    { label: "alias", specifier: "@/leaf", target: "src/leaf.ts" },
    { label: "directory index", specifier: "./leaf", target: "src/leaf/index.ts" },
    { label: "JavaScript extension", specifier: "./leaf.js", target: "src/leaf.ts" },
    { label: "workspace root", specifier: "@printstash/ui", target: "packages/ui/src/index.ts" },
    {
      label: "workspace exact",
      specifier: "@printstash/domain/format",
      target: "packages/domain/src/format.ts",
    },
    {
      label: "workspace wildcard",
      specifier: "@printstash/ui/button",
      target: "packages/ui/src/components/button.tsx",
    },
  ])("resolves first-party module identities: $label", ({ specifier, target }) => {
    const result = inspect({
      "src/entry.ts": `import { value } from "${specifier}";`,
      [target]: "export const value = 1;",
    });

    expect(result.edges).toMatchObject([{ from: "src/entry.ts", target, line: 1 }]);
    expect(result.diagnostics).toEqual([]);
  });

  it.each([
    {
      label: "static",
      source: 'import { value } from "./leaf";',
      kind: "static",
      symbols: ["value"],
    },
    { label: "side effect", source: 'import "./leaf";', kind: "static", symbols: [] },
    {
      label: "named re-export",
      source: 'export { value } from "./leaf";',
      kind: "reexport",
      symbols: ["value"],
    },
    {
      label: "star re-export",
      source: 'export * from "./leaf";',
      kind: "reexport",
      symbols: ["*"],
    },
    {
      label: "dynamic",
      source: 'const load = () => import("./leaf");',
      kind: "dynamic",
      symbols: ["*"],
    },
    { label: "literal template", source: "import(`./leaf`);", kind: "dynamic", symbols: ["*"] },
    {
      label: "require",
      source: 'const value = require("./leaf");',
      kind: "static",
      symbols: ["*"],
    },
    {
      label: "TS require",
      source: 'import value = require("./leaf");',
      kind: "static",
      symbols: ["*"],
    },
    {
      label: "type expression",
      source: 'type T = import("./leaf").Value;',
      kind: "type",
      symbols: ["*"],
    },
  ])("discovers supported import syntax: $label", ({ source, kind, symbols }) => {
    const result = inspect({
      "src/entry.ts": `\n${source}`,
      "src/leaf.ts": "export const value = 1;",
    });

    expect(result.edges).toMatchObject([
      { from: "src/entry.ts", target: "src/leaf.ts", line: 2, kind, symbols },
    ]);
  });

  it.each([
    { label: "type import", source: 'import type { Value } from "./leaf";', typeOnly: true },
    {
      label: "specifier type import",
      source: 'import { type Value } from "./leaf";',
      typeOnly: true,
    },
    {
      label: "mixed import",
      source: 'import { type Value, value } from "./leaf";',
      typeOnly: false,
    },
    { label: "type export", source: 'export type { Value } from "./leaf";', typeOnly: true },
    {
      label: "specifier type export",
      source: 'export { type Value } from "./leaf";',
      typeOnly: true,
    },
    {
      label: "mixed export",
      source: 'export { type Value, value } from "./leaf";',
      typeOnly: false,
    },
    { label: "star type export", source: 'export type * from "./leaf";', typeOnly: true },
    { label: "type expression", source: 'type T = import("./leaf").Value;', typeOnly: true },
  ])("distinguishes explicit type-only edges: $label", ({ source, typeOnly }) => {
    const result = inspect({ "src/entry.ts": source, "src/leaf.ts": "export const value = 1;" });

    expect(result.edges).toMatchObject([{ typeOnly }]);
  });

  it("excludes nonproduction graph roots", () => {
    const result = inspect({
      "src/entry.ts": "export const value = 1;",
      "src/__tests__/entry.test.ts": 'import("missing");',
      "src/entry.spec.ts": 'import("missing");',
      "src/test-support/fixtures.ts": 'import("missing");',
      "src/generated/types.ts": 'import("missing");',
      "src/env.d.ts": 'import("missing");',
      "src/node_modules/dep/index.ts": 'import("missing");',
      "src/artifacts/file.ts": 'import("missing");',
      "packages/ui/dist/index.ts": 'import("missing");',
      "tests/repo/a.test.ts": 'import("missing");',
    });

    expect(result.files).toEqual(["src/entry.ts"]);
    expect(result.edges).toEqual([]);
  });

  it.each([
    {
      label: "package alias",
      from: "packages/ui/src/index.ts",
      target: "src/lib/value.ts",
      specifier: "@/lib/value",
    },
    {
      label: "package relative",
      from: "packages/ui/src/index.ts",
      target: "src/lib/value.ts",
      specifier: "../../../src/lib/value",
    },
    {
      label: "type-only package",
      from: "packages/domain/src/index.ts",
      target: "src/types/value.ts",
      specifier: "@/types/value",
    },
    {
      label: "domain UI",
      from: "packages/domain/src/index.ts",
      target: "packages/ui/src/index.ts",
      specifier: "@printstash/ui",
    },
    {
      label: "workspace private relative",
      from: "src/entry.ts",
      target: "packages/ui/src/lib/private.ts",
      specifier: "../packages/ui/src/lib/private",
    },
  ])("rejects package boundary bypasses: $label", ({ from, target, specifier }) => {
    const result = edge(from, target, `import type { Value } from "${specifier}";`);

    expect(result.diagnostics).toMatchObject([{ code: "boundary", from, line: 1 }]);
  });

  it("rejects undeclared workspace entry points", () => {
    const result = inspect({
      "src/entry.ts": 'import { value } from "@printstash/domain/private";',
      "packages/domain/src/private.ts": "export const value = 1;",
    });

    expect(result.diagnostics).toEqual([
      {
        code: "unresolved",
        from: "src/entry.ts",
        line: 1,
        message: "Cannot resolve @printstash/domain/private",
      },
    ]);
  });

  it.each([
    { label: "React", from: "src/lib/api/request.ts", target: "react", specifier: "react" },
    {
      label: "Query",
      from: "src/lib/api/models.ts",
      target: "@tanstack/react-query",
      specifier: "@tanstack/react-query",
    },
    {
      label: "components",
      from: "src/lib/api/models.ts",
      target: "src/components/card.tsx",
      specifier: "@/components/card",
    },
    {
      label: "routes",
      from: "src/lib/api/models.ts",
      target: "src/pages/setup.tsx",
      specifier: "@/pages/setup",
    },
    {
      label: "features",
      from: "src/lib/api/models.ts",
      target: "src/features/setup/entry.ts",
      specifier: "@/features/setup/entry",
    },
    {
      label: "transport to endpoint",
      from: "src/lib/api/request.ts",
      target: "src/lib/api/models.ts",
      specifier: "@/lib/api/models",
    },
  ])("rejects upward transport dependencies: $label", ({ from, target, specifier }) => {
    const result = edge(from, target, `export * from "${specifier}";`);

    expect(result.diagnostics).toMatchObject([{ code: "boundary", from, line: 1 }]);
  });

  it.each(["session-transport", "auth-store", "events"])(
    "rejects infrastructure feature dependencies: %s",
    (name) => {
      const result = edge(
        `src/lib/${name}.ts`,
        "src/features/setup/entry.ts",
        'import("@/features/setup/entry");',
      );

      expect(result.diagnostics).toMatchObject([{ code: "boundary", from: `src/lib/${name}.ts` }]);
    },
  );

  it.each(["src/pages/setup.tsx", "src/features/auth/entry.ts"])(
    "rejects private feature consumption: %s",
    (from) => {
      const result = edge(
        from,
        "src/features/setup/private.ts",
        'import type { Value } from "@/features/setup/private";',
      );

      expect(result.diagnostics).toMatchObject([
        {
          code: "boundary",
          message: "Feature internals are private: src/features/setup/private.ts",
        },
      ]);
    },
  );

  it.each(["src/pages/setup.tsx", "src/main.tsx", "src/router.tsx"])(
    "rejects feature route composition dependencies: %s",
    (target) => {
      const result = edge("src/features/setup/entry.ts", target, `import("@/${target.slice(4)}");`);

      expect(result.diagnostics).toMatchObject([
        { code: "boundary", from: "src/features/setup/entry.ts" },
      ]);
    },
  );

  it("rejects upward shared-contract dependencies", () => {
    const result = edge(
      "src/types/config.ts",
      "src/lib/api/config.ts",
      'export type { Value } from "@/lib/api/config";',
    );

    expect(result.diagnostics).toMatchObject([{ code: "boundary", from: "src/types/config.ts" }]);
  });

  it.each([
    {
      label: "static",
      files: { "src/a.ts": 'import "./b";', "src/b.ts": 'import "./a";' },
      members: ["src/a.ts", "src/b.ts"],
      connections: [
        ["src/a.ts", "src/b.ts"],
        ["src/b.ts", "src/a.ts"],
      ],
    },
    {
      label: "lazy",
      files: { "src/a.ts": 'import("./b");', "src/b.ts": 'import "./a";' },
      members: ["src/a.ts", "src/b.ts"],
      connections: [
        ["src/a.ts", "src/b.ts"],
        ["src/b.ts", "src/a.ts"],
      ],
    },
    {
      label: "self",
      files: { "src/a.ts": 'import "./a";', "src/b.ts": "" },
      members: ["src/a.ts"],
      connections: [["src/a.ts", "src/a.ts"]],
    },
  ])("reports runtime strongly connected components: $label", ({ files, members, connections }) => {
    const result = inspect(files);

    expect(result.runtimeCycles.map((group) => group.members)).toEqual([members]);
    expect(result.runtimeCycles[0].edges.map((item) => [item.from, item.target])).toEqual(
      connections,
    );
    expect(result.diagnostics).toMatchObject([{ code: "runtime-cycle" }]);
  });

  it("reports cycles involving type-only edges", () => {
    const result = inspect({
      "src/a.ts": 'import type { Value } from "./b";',
      "src/b.ts": 'import "./a";',
    });

    expect(result.runtimeCycles).toEqual([]);
    expect(result.allCycles.map((group) => group.members)).toEqual([["src/a.ts", "src/b.ts"]]);
    expect(result.diagnostics).toMatchObject([{ code: "type-cycle" }]);
  });

  it.each([
    {
      label: "dynamic",
      source: 'const name = "./b"; import(name);',
      message: "Review unresolved dynamic import: name",
    },
    {
      label: "worker module",
      source: "new Worker(new URL(name, import.meta.url));",
      message: "Review unresolved worker import: name",
    },
    {
      label: "worker base",
      source: 'new Worker(new URL("./worker.ts", location.href));',
      message: "Review worker URL that is not relative to import.meta.url",
    },
    {
      label: "direct worker",
      source: "new Worker(name);",
      message: "Review worker URL that is not relative to import.meta.url",
    },
  ])("reports unresolved computed imports: $label", ({ source, message }) => {
    const result = inspect({ "src/a.ts": `\n${source}` });

    expect(result.diagnostics).toEqual([{ code: "computed", from: "src/a.ts", line: 2, message }]);
  });

  it.each(["./missing", "@/missing"])("rejects unresolved first-party imports: %s", (specifier) => {
    const result = inspect({ "src/a.ts": `import "${specifier}";` });

    expect(result.diagnostics).toEqual([
      { code: "unresolved", from: "src/a.ts", line: 1, message: `Cannot resolve ${specifier}` },
    ]);
  });

  it("rejects parser failures", () => {
    const result = inspect({ "src/a.ts": "const = ;" });

    expect(result.diagnostics).toMatchObject([{ code: "parse", from: "src/a.ts", line: 1 }]);
  });

  const legacyFixture = [
    {
      from: "src/lib/api/request.ts",
      target: "src/lib/query-client.ts",
      symbols: ["queryClient", "invalidateQueriesForPath"],
      reason: "Synthetic exception engine fixture",
      removeBy: "M10 fixture",
    },
  ];
  it("prohibits transport Query imports without migration exceptions", () => {
    expect(MIGRATION_EXCEPTIONS).toEqual([]);
    const result = inspectDependencies(
      project({
        "src/lib/api/request.ts": 'import { queryClient } from "@/lib/query-client";',
        "src/lib/query-client.ts": "export const queryClient = {};",
      }),
      MIGRATION_EXCEPTIONS,
    );
    expect(result.diagnostics.map((item) => item.code)).toEqual(["boundary"]);
  });
  it.each([
    {
      label: "exact",
      source: 'import { queryClient, invalidateQueriesForPath } from "@/lib/query-client";',
      expected: [],
    },
    {
      label: "renamed local bindings",
      source:
        'import { queryClient as cache, invalidateQueriesForPath as invalidate } from "@/lib/query-client";',
      expected: [],
    },
    {
      label: "extra symbol",
      source:
        'import { queryClient, invalidateQueriesForPath, queryKeys } from "@/lib/query-client";',
      expected: ["boundary", "stale-exception"],
    },
    {
      label: "namespace",
      source: 'import * as client from "@/lib/query-client";',
      expected: ["boundary", "stale-exception"],
    },
    {
      label: "changed target",
      source: 'import { queryClient, invalidateQueriesForPath } from "@/lib/queries";',
      expected: ["boundary", "stale-exception"],
    },
    {
      label: "type-only substitution",
      source: 'import type { queryClient, invalidateQueriesForPath } from "@/lib/query-client";',
      expected: ["boundary", "stale-exception"],
    },
  ])("limits an exception to its declared edge: $label", ({ source, expected }) => {
    const result = inspectDependencies(
      project({
        "src/lib/api/request.ts": source,
        "src/lib/query-client.ts": "export const queryClient = {};",
        "src/lib/queries.ts": "export const queryClient = {};",
      }),
      legacyFixture,
    );

    expect(result.diagnostics.map((item) => item.code)).toEqual(expected);
  });

  it("rejects stale exceptions", () => {
    const result = inspectDependencies(
      project({ "src/lib/api/request.ts": "export const value = 1;" }),
      legacyFixture,
    );

    expect(result.diagnostics).toMatchObject([
      {
        code: "stale-exception",
        from: "src/lib/api/request.ts",
        message: expect.stringContaining("M10"),
      },
    ]);
  });

  it.each(["Worker", "SharedWorker"])("discovers literal worker module loads: %s", (worker) => {
    const result = inspect({
      "src/a.ts": `new ${worker}(new URL("./worker.ts", import.meta.url), { type: "module" });`,
      "src/worker.ts": "export const value = 1;",
    });

    expect(result.edges).toEqual([
      {
        from: "src/a.ts",
        target: "src/worker.ts",
        specifier: "./worker.ts",
        kind: "worker",
        typeOnly: false,
        line: 1,
        symbols: ["*"],
      },
    ]);
    expect(result.diagnostics).toEqual([]);
  });

  it.each(["src/test-support/factory.ts", "src/lib/__tests__/fixture.ts", "src/lib/value.test.ts"])(
    "rejects production test dependencies: %s",
    (target) => {
      const result = edge("src/entry.ts", target, `import "@/${target.slice(4)}";`);

      expect(result.diagnostics).toMatchObject([
        {
          code: "boundary",
          from: "src/entry.ts",
          message: `Production cannot import test or fixture modules: ${target}`,
        },
      ]);
    },
  );

  it("enforces the production repository graph", () => {
    const result = inspectDependencies(readDependencyProject(resolve(__dirname, "../..")));

    expect(result.diagnostics).toEqual([]);
  });
});

describe("readDependencyProject", () => {
  afterEach(() => {
    for (const root of workspaceRoots.splice(0)) rmSync(root, { recursive: true, force: true });
  });

  it("rejects undisclosed workspace packages", () => {
    const root = workspaceFixture(["ui", "domain", "new-package"], 'packages:\n  - "packages/*"\n');

    expect(() => readDependencyProject(root)).toThrow(
      "Workspace package roots changed: domain,new-package,ui; update the dependency boundary policy",
    );
  });

  it("rejects changed workspace source patterns", () => {
    const root = workspaceFixture(["ui", "domain"], 'packages:\n  - "packages/*"\n  - "other/*"\n');

    expect(() => readDependencyProject(root)).toThrow(
      "Workspace patterns changed; update the dependency boundary source roots",
    );
  });
});
