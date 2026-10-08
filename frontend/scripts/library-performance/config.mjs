/** Build explicit expectations from the real fixture corpus, never from the rendered DOM. */
import { readFile, writeFile } from "node:fs/promises";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
const [distributedFile, richFile, output] = process.argv.slice(2);
if (!output)
  throw new Error(
    "Usage: node scripts/library-performance/config.mjs DISTRIBUTED_CORPUS RICH_CORPUS OUTPUT",
  );
const sources = await Promise.all(
  [distributedFile, richFile].map((path) => readFile(path, "utf8")),
);
const [distributed, rich] = sources.map(JSON.parse);
function target(manifest, { path = null, expanded = false, rows = [], folders, query = "" } = {}) {
  const collection = manifest.collections.find((c) => c.path === path);
  const params = new URLSearchParams();
  if (path) params.set("c", path);
  if (query) params.set("q", query);
  const result = {
    url: params.size ? `/?${params}` : "/",
    collection: path,
    query,
    expanded: expanded ? manifest.collections.map((c) => c.path) : [],
    titles: { en: collection?.name ?? "All Models", es: collection?.name ?? "Todos los modelos" },
    branches: manifest.collections
      .filter((c) => expanded || !c.path.includes("/"))
      .map((c) => c.name),
    branchPaths: manifest.collections
      .filter((c) => expanded || !c.path.includes("/"))
      .map((c) => c.path),
    leaves: expanded ? manifest.model_rows.slice(90).map((m) => m.name) : [],
    leafPaths: expanded ? manifest.model_rows.slice(90).map((m) => `/models/${m.id}`) : [],
    entries: rows.map((m) => ({ path: `/models/${m.id}`, media: m.media })),
  };
  if (folders) result.folders = folders;
  return result;
}
const dense = target(rich, { path: rich.dense, rows: rich.model_rows.slice(0, 24) });
const deep = target(rich, { path: rich.deep, expanded: true, rows: rich.model_rows.slice(90) });
const denseExpanded = target(rich, {
  path: rich.dense,
  expanded: true,
  rows: rich.model_rows.slice(0, 24),
});
const search = {
  ...target(rich, {
    path: rich.dense,
    rows: rich.model_rows.slice(0, 1),
    query: "Benchmark model 001",
  }),
};
const git = (args) => execFileSync("git", args, { encoding: "utf8" }).trim();
const config = {
  identity: {
    commit: git(["rev-parse", "HEAD"]),
    dirty: git(["status", "--porcelain"]).length > 0,
    image: process.env.PERF_IMAGE ?? null,
    corpus: createHash("sha256").update(sources.join("\n")).digest("hex"),
  },
  scenarios: [
    {
      name: "distributed",
      base: process.env.PERF_DISTRIBUTED_URL ?? "http://127.0.0.1:3520",
      target: target(distributed, { folders: distributed.collections.map((c) => c.path) }),
    },
    {
      name: "dense",
      base: process.env.PERF_RICH_URL ?? "http://127.0.0.1:3521",
      target: dense,
      journeys: {
        collection: { from: denseExpanded, target: deep, button: "Depth 07" },
        back: {
          from: dense,
          target: dense,
          model: `/models/${rich.model_rows[0].id}`,
          modelName: rich.model_rows[0].name,
        },
        page: {
          from: dense,
          target: target(rich, { path: rich.dense, rows: rich.model_rows.slice(0, 48) }),
        },
        search: { from: dense, target: search },
      },
    },
    { name: "deep", base: process.env.PERF_RICH_URL ?? "http://127.0.0.1:3521", target: deep },
  ],
};
await writeFile(output, JSON.stringify(config, null, 2));
