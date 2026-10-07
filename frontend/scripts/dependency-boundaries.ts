import { existsSync, readFileSync, readdirSync } from "node:fs";
import { join, posix } from "node:path";
import { parseSync, Visitor, type Expression } from "oxc-parser";

/** Paths are frontend-relative, including resolved workspace exports. */
export interface WorkspacePackage {
  name: string;
  directory: string;
  exports: Record<string, string>;
}
export interface DependencyProject {
  files: ReadonlyMap<string, string>;
  aliases: Record<string, string[]>;
  packages: readonly WorkspacePackage[];
}
export interface DependencyEdge {
  from: string;
  line: number;
  specifier: string;
  target: string;
  kind: "static" | "reexport" | "dynamic" | "type" | "worker";
  typeOnly: boolean;
  symbols: string[];
}
export interface DependencyDiagnostic {
  code:
    | "parse"
    | "unresolved"
    | "computed"
    | "boundary"
    | "runtime-cycle"
    | "type-cycle"
    | "stale-exception";
  from: string;
  line: number;
  message: string;
}
export interface MigrationException {
  from: string;
  target: string;
  symbols: readonly string[];
  reason: string;
  removeBy: string;
}
export const MIGRATION_EXCEPTIONS: readonly MigrationException[] = [
  {
    from: "src/lib/api/request.ts",
    target: "src/lib/query-client.ts",
    symbols: ["queryClient", "invalidateQueriesForPath"],
    reason: "Legacy HTTP mutations still delegate invalidation to the central Query policy.",
    removeBy: "M10: migrate the remaining mutation callers to their feature policy.",
  },
];

/** Existing public modules, not a blanket exemption for future feature internals. */
export const FEATURE_PUBLIC_MODULES = new Set([
  "src/features/auth/entry.ts",
  "src/features/setup/entry.ts",
  "src/features/work/queries.ts",
  "src/features/printers/queries.ts",
  ...[
    "authority",
    "batch-edits",
    "browse",
    "filters",
    "model-detail",
    "moves",
    "multipart",
    "provenance",
    "mutations",
    "navigation-state",
    "reading-position",
    "saved-views",
    "thumbnails",
    "url",
  ].map((name) => `src/features/library/${name}.ts`),
  "src/features/library/navigation.tsx",
]);
const ROOTS = ["src", "packages/ui/src", "packages/domain/src"];
const EXCLUDED = new Set([
  "__tests__",
  "tests",
  "test-support",
  "__fixtures__",
  "generated",
  "node_modules",
  "dist",
  "coverage",
  "artifacts",
]);
function production(file: string): boolean {
  return (
    ROOTS.some((root) => file.startsWith(`${root}/`)) &&
    !file.split("/").some((part) => EXCLUDED.has(part) || part.startsWith(".")) &&
    /\.(?:[cm]?[jt]s|[jt]sx)$/.test(file) &&
    !/\.(?:test|spec|d)\.[cm]?[jt]sx?$/.test(file)
  );
}

/** Read source roots, never installed dependencies or build output. */
export function readDependencyProject(root: string): DependencyProject {
  const workspace = readFileSync(join(root, "pnpm-workspace.yaml"), "utf8");
  const packageSection = workspace.match(/^packages:[ \t]*\r?\n((?:[ \t]+[^\n]*(?:\n|$))*)/m);
  const declaredPatterns = packageSection?.[1].trim().split(/\r?\n/);
  if (
    !declaredPatterns ||
    declaredPatterns.length !== 1 ||
    !/^-\s*["']?packages\/\*["']?$/.test(declaredPatterns[0].trim())
  ) {
    throw new Error("Workspace patterns changed; update the dependency boundary source roots");
  }
  const packageNames = readdirSync(join(root, "packages"), { withFileTypes: true })
    .filter(
      (entry) =>
        entry.isDirectory() && existsSync(join(root, "packages", entry.name, "package.json")),
    )
    .map((entry) => entry.name)
    .sort();
  if (packageNames.join(",") !== "domain,ui") {
    throw new Error(
      `Workspace package roots changed: ${packageNames.join(",")}; update the dependency boundary policy`,
    );
  }
  const files = new Map<string, string>();
  const visit = (directory: string) => {
    for (const entry of readdirSync(join(root, directory), { withFileTypes: true })) {
      if (
        entry.name.startsWith(".") ||
        ["node_modules", "dist", "coverage", "artifacts"].includes(entry.name)
      )
        continue;
      const file = `${directory}/${entry.name}`;
      if (entry.isDirectory()) visit(file);
      else if (entry.isFile())
        files.set(file, production(file) ? readFileSync(join(root, file), "utf8") : "");
    }
  };
  ROOTS.forEach(visit);
  const config: { compilerOptions: { paths: Record<string, string[]> } } = JSON.parse(
    readFileSync(join(root, "tsconfig.json"), "utf8"),
  );
  const packages = ["ui", "domain"].map((name) => {
    const directory = `packages/${name}`;
    const manifest: { name: string; exports: Record<string, string> } = JSON.parse(
      readFileSync(join(root, directory, "package.json"), "utf8"),
    );
    return { directory, name: manifest.name, exports: manifest.exports };
  });
  return { files, aliases: config.compilerOptions.paths, packages };
}
function substitute(pattern: string, value: string, replacement: string): string | null {
  const star = pattern.indexOf("*");
  if (star === -1) return pattern === value ? replacement : null;
  const prefix = pattern.slice(0, star),
    suffix = pattern.slice(star + 1);
  if (!value.startsWith(prefix) || !value.endsWith(suffix)) return null;
  return replacement.replace("*", value.slice(prefix.length, value.length - suffix.length));
}
function resolveTarget(project: DependencyProject, from: string, specifier: string): string | null {
  const clean = specifier.replace(/[?#].*$/, "");
  let candidates: string[] = [];
  if (clean.startsWith(".")) candidates = [posix.join(posix.dirname(from), clean)];
  else {
    for (const [alias, replacements] of Object.entries(project.aliases)) {
      candidates.push(
        ...replacements.flatMap((replacement) => {
          const match = substitute(alias, clean, replacement);
          return match === null ? [] : [match];
        }),
      );
    }
    const workspace = project.packages.find(
      (pkg) => clean === pkg.name || clean.startsWith(`${pkg.name}/`),
    );
    if (workspace) {
      const subpath = clean === workspace.name ? "." : `.${clean.slice(workspace.name.length)}`;
      const exact = workspace.exports[subpath];
      const matches = exact
        ? [exact]
        : Object.entries(workspace.exports).flatMap(([key, value]) => {
            const match = substitute(key, subpath, value);
            return match === null ? [] : [match];
          });
      candidates = matches.map((match) => posix.join(workspace.directory, match));
      if (!candidates.length) return null;
    } else if (!candidates.length)
      return clean.startsWith("@/") || clean.startsWith("@printstash/") ? null : clean;
  }
  for (const candidate of candidates) {
    const base = posix.normalize(candidate);
    const stem = base.replace(/\.[cm]?jsx?$/, "");
    const paths = [
      base,
      ...[
        ".ts",
        ".tsx",
        ".mts",
        ".cts",
        ".js",
        ".jsx",
        ".mjs",
        ".cjs",
        "/index.ts",
        "/index.tsx",
        "/index.js",
      ].map((ext) => stem + ext),
    ];
    const found = paths.find((file) => project.files.has(file));
    if (found) return found;
  }
  return null;
}
function feature(file: string): string | null {
  return file.startsWith("src/features/") ? file.split("/")[2] : null;
}
function composition(file: string): boolean {
  return file.startsWith("src/pages/") || ["src/main.tsx", "src/router.tsx"].includes(file);
}
function boundary(edge: DependencyEdge, packages: readonly WorkspacePackage[]): string | null {
  const { from, target } = edge;
  const targetFeature = feature(target);
  if (
    target
      .split("/")
      .some((part) => ["__tests__", "tests", "test-support", "__fixtures__"].includes(part)) ||
    /\.(test|spec)\.[cm]?[jt]sx?$/.test(target)
  )
    return "Production cannot import test or fixture modules";
  const targetPackage = packages.find((pkg) => target.startsWith(`${pkg.directory}/`));
  if (
    targetPackage &&
    !from.startsWith(`${targetPackage.directory}/`) &&
    !(edge.specifier === targetPackage.name || edge.specifier.startsWith(`${targetPackage.name}/`))
  )
    return "Workspace consumers must use declared package exports";
  if (from.startsWith("packages/") && target.startsWith("src/"))
    return "Workspace packages cannot import app source";
  if (
    from.startsWith("packages/domain/") &&
    (target.startsWith("packages/ui/") ||
      /^(react(?:-dom|-router-dom)?|@tanstack\/react-query)(\/|$)/.test(target))
  )
    return "Domain cannot depend on UI frameworks";
  if (from.startsWith("src/lib/api/")) {
    const lowLevel = [
      "src/lib/auth.ts",
      "src/lib/auth-store.ts",
      "src/lib/session-transport.ts",
      "src/lib/errors.ts",
    ];
    const allowed =
      target.startsWith("src/types/") ||
      target.startsWith("src/generated/") ||
      lowLevel.includes(target) ||
      (from !== "src/lib/api/request.ts" && target.startsWith("src/lib/api/"));
    if (!allowed)
      return "Endpoint and transport dependencies must remain below query and application policy";
  }
  if (
    [
      "src/lib/session-transport.ts",
      "src/lib/auth-store.ts",
      "src/lib/auth.ts",
      "src/lib/events.ts",
    ].includes(from) &&
    (targetFeature || composition(target) || target.startsWith("src/components/"))
  )
    return "Session and event infrastructure cannot depend on application composition";
  if (feature(from) && composition(target)) return "Features cannot import route composition";
  if (targetFeature && feature(from) !== targetFeature && !FEATURE_PUBLIC_MODULES.has(target))
    return "Feature internals are private";
  if (
    from.startsWith("src/types/") &&
    !(
      target.startsWith("src/types/") ||
      target.startsWith("src/generated/") ||
      target.startsWith("packages/domain/")
    )
  )
    return "Shared contracts cannot depend on application implementation";
  return null;
}
export interface DependencyCycle {
  members: string[];
  edges: DependencyEdge[];
}
function cycles(files: readonly string[], edges: readonly DependencyEdge[]): DependencyCycle[] {
  const adjacency = new Map<string, DependencyEdge[]>(files.map((file) => [file, []]));
  for (const edge of edges) if (adjacency.has(edge.target)) adjacency.get(edge.from)?.push(edge);
  let next = 0;
  const ids = new Map<string, number>(),
    low = new Map<string, number>(),
    stack: string[] = [],
    active = new Set<string>();
  const result: DependencyCycle[] = [];
  function visit(file: string) {
    const id = next++;
    ids.set(file, id);
    low.set(file, id);
    stack.push(file);
    active.add(file);
    for (const edge of adjacency.get(file) ?? []) {
      if (!ids.has(edge.target)) {
        visit(edge.target);
        low.set(file, Math.min(low.get(file)!, low.get(edge.target)!));
      } else if (active.has(edge.target))
        low.set(file, Math.min(low.get(file)!, ids.get(edge.target)!));
    }
    if (low.get(file) !== ids.get(file)) return;
    const members: string[] = [];
    let member: string;
    do {
      member = stack.pop()!;
      active.delete(member);
      members.push(member);
    } while (member !== file);
    const group = new Set(members);
    const inside = edges.filter((edge) => group.has(edge.from) && group.has(edge.target));
    if (members.length > 1 || inside.some((edge) => edge.from === edge.target))
      result.push({ members: members.sort(), edges: inside });
  }
  for (const file of files) if (!ids.has(file)) visit(file);
  return result;
}

/** Fail closed on imports that cannot participate in the boundary proof. */
export function inspectDependencies(
  project: DependencyProject,
  exceptions: readonly MigrationException[] = MIGRATION_EXCEPTIONS,
) {
  const files = [...project.files.keys()].filter(production).sort();
  const edges: DependencyEdge[] = [],
    diagnostics: DependencyDiagnostic[] = [];
  for (const from of files) {
    const source = project.files.get(from)!;
    const parsed = parseSync(from, source, { lang: from.endsWith("x") ? "tsx" : "ts" });
    const lineAt = (start: number) => source.slice(0, start).split("\n").length;
    for (const error of parsed.errors)
      diagnostics.push({
        code: "parse",
        from,
        line: error.labels[0] ? lineAt(error.labels[0].start) : 1,
        message: error.message,
      });
    const add = (
      expression: Expression,
      start: number,
      kind: DependencyEdge["kind"],
      typeOnly: boolean,
      symbols: string[],
    ) => {
      const line = lineAt(start);
      const specifier =
        expression.type === "Literal" &&
        (expression.raw?.startsWith('"') || expression.raw?.startsWith("'"))
          ? String(expression.value)
          : expression.type === "TemplateLiteral" && expression.expressions.length === 0
            ? expression.quasis[0].value.cooked
            : null;
      if (specifier === null || specifier === undefined) {
        diagnostics.push({
          code: "computed",
          from,
          line,
          message: `Review unresolved ${kind} import: ${source.slice(expression.start, expression.end)}`,
        });
        return;
      }
      const target = resolveTarget(project, from, specifier);
      if (target === null) {
        diagnostics.push({
          code: "unresolved",
          from,
          line,
          message: `Cannot resolve ${specifier}`,
        });
        return;
      }
      edges.push({ from, line, specifier, target, kind, typeOnly, symbols: symbols.sort() });
    };
    new Visitor({
      ImportDeclaration(node) {
        add(
          node.source,
          node.start,
          "static",
          node.importKind === "type" ||
            (node.specifiers.length > 0 &&
              node.specifiers.every(
                (s) => s.type === "ImportSpecifier" && s.importKind === "type",
              )),
          node.specifiers.map((s) =>
            s.type === "ImportSpecifier"
              ? s.imported.type === "Identifier"
                ? s.imported.name
                : String(s.imported.value)
              : s.type === "ImportDefaultSpecifier"
                ? "default"
                : "*",
          ),
        );
      },
      ExportNamedDeclaration(node) {
        if (node.source)
          add(
            node.source,
            node.start,
            "reexport",
            node.exportKind === "type" ||
              (node.specifiers.length > 0 && node.specifiers.every((s) => s.exportKind === "type")),
            node.specifiers.map((s) =>
              s.local.type === "Identifier" ? s.local.name : String(s.local.value),
            ),
          );
      },
      ExportAllDeclaration(node) {
        add(node.source, node.start, "reexport", node.exportKind === "type", ["*"]);
      },
      ImportExpression(node) {
        add(node.source, node.start, "dynamic", false, ["*"]);
      },
      TSImportType(node) {
        add(node.source, node.start, "type", true, ["*"]);
      },
      TSImportEqualsDeclaration(node) {
        if (node.moduleReference.type === "TSExternalModuleReference")
          add(node.moduleReference.expression, node.start, "static", node.importKind === "type", [
            "*",
          ]);
      },
      CallExpression(node) {
        if (node.callee.type !== "Identifier" || node.callee.name !== "require") return;
        const argument = node.arguments[0];
        if (argument && argument.type !== "SpreadElement")
          add(argument, node.start, "static", false, ["*"]);
      },
      NewExpression(node) {
        if (
          node.callee.type !== "Identifier" ||
          !["Worker", "SharedWorker"].includes(node.callee.name)
        )
          return;
        const url = node.arguments[0];
        if (
          url?.type === "NewExpression" &&
          url.callee.type === "Identifier" &&
          url.callee.name === "URL"
        ) {
          const module = url.arguments[0],
            base = url.arguments[1];
          const moduleRelative =
            base?.type === "MemberExpression" &&
            !base.computed &&
            base.property.type === "Identifier" &&
            base.property.name === "url" &&
            base.object.type === "MetaProperty" &&
            base.object.meta.name === "import" &&
            base.object.property.name === "meta";
          if (module && module.type !== "SpreadElement" && moduleRelative) {
            add(module, node.start, "worker", false, ["*"]);
            return;
          }
        }
        diagnostics.push({
          code: "computed",
          from,
          line: lineAt(node.start),
          message: "Review worker URL that is not relative to import.meta.url",
        });
      },
    }).visit(parsed.program);
  }
  const used = new Set<MigrationException>();
  for (const edge of edges) {
    const violation = boundary(edge, project.packages);
    if (!violation) continue;
    const exception = exceptions.find(
      (item) =>
        item.from === edge.from &&
        item.target === edge.target &&
        [...item.symbols].sort().join("\0") === edge.symbols.join("\0") &&
        edge.kind === "static" &&
        !edge.typeOnly,
    );
    if (exception) used.add(exception);
    else
      diagnostics.push({
        code: "boundary",
        from: edge.from,
        line: edge.line,
        message: `${violation}: ${edge.target}`,
      });
  }
  for (const exception of exceptions)
    if (!used.has(exception))
      diagnostics.push({
        code: "stale-exception",
        from: exception.from,
        line: 1,
        message: `Remove unused migration exception for ${exception.target}: ${exception.removeBy}`,
      });
  const runtimeCycles = cycles(
    files,
    edges.filter((edge) => !edge.typeOnly),
  );
  const allCycles = cycles(files, edges);
  for (const group of runtimeCycles)
    diagnostics.push({
      code: "runtime-cycle",
      from: group.members[0],
      line: 1,
      message: group.members.join(" -> "),
    });
  for (const group of allCycles.filter((group) => group.edges.some((edge) => edge.typeOnly)))
    diagnostics.push({
      code: "type-cycle",
      from: group.members[0],
      line: 1,
      message: group.members.join(" -> "),
    });
  return { files, edges, diagnostics, runtimeCycles, allCycles };
}
