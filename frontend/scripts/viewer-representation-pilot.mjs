/** Isolated, disposable browser pilot. No dependency installation or product server. */
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { createReadStream } from "node:fs";
import { mkdir, readFile, writeFile, mkdtemp, rename, rm, stat } from "node:fs/promises";
import http from "node:http";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendDirectory = path.resolve(scriptDirectory, "..");
const QUALITY_POLICY = Object.freeze({
  position_tolerance_mm: 1e-5,
  normal_tolerance: 1e-6,
  source_bounds_tolerance_mm: 1e-5,
  foreground_mask_max_differing_pixels: 0,
  rgba_max_component_difference: 1,
  observation_trial: 0,
  raw_hash_scope: "diagnostic only, preserves signed zero and raw normal bits",
});
function options(argv) {
  const values = new Map();
  for (let index = 0; index < argv.length; index += 2) {
    const key = argv[index];
    if (
      !["--runtime-dir", "--manifest", "--output", "--repeat", "--timeout-ms"].includes(key) ||
      !argv[index + 1] ||
      values.has(key)
    )
      throw new Error(`invalid_argument:${key}`);
    values.set(key, argv[index + 1]);
  }
  for (const key of ["--runtime-dir", "--manifest", "--output"])
    if (!values.has(key)) throw new Error(`missing_argument:${key}`);
  const repeat = Number(values.get("--repeat") ?? 1);
  const timeout = Number(values.get("--timeout-ms") ?? 600000);
  if (!Number.isSafeInteger(repeat) || repeat < 1 || repeat > 100)
    throw new Error("repeat_must_be_1_to_100");
  if (!Number.isSafeInteger(timeout) || timeout < 1000 || timeout > 3600000)
    throw new Error("timeout_must_be_1000_to_3600000_ms");
  return {
    runtime: path.resolve(values.get("--runtime-dir")),
    manifest: path.resolve(values.get("--manifest")),
    output: path.resolve(values.get("--output")),
    repeat,
    timeout,
  };
}
async function hashFile(filename) {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(filename)) hash.update(chunk);
  return hash.digest("hex");
}
async function packageVersion(runtime, name) {
  return JSON.parse(
    await readFile(path.join(runtime, "node_modules", name, "package.json"), "utf8"),
  ).version;
}
function parseManifest(text, z) {
  const header = z
    .object({ schema_version: z.literal(1), cases: z.array(z.unknown()).min(1).max(100) })
    .passthrough()
    .safeParse(JSON.parse(text));
  if (!header.success) throw new Error("invalid_manifest_v1");
  const vector = z.tuple([z.number().finite(), z.number().finite(), z.number().finite()]);
  const caseSchema = z
    .object({
      case_id: z.string().min(1),
      source_sha256: z.string().regex(/^[a-f0-9]{64}$/),
      reference_path: z.string().refine(path.isAbsolute),
      candidate_path: z.string().refine(path.isAbsolute),
      triangle_count: z.number().int().positive().max(Number.MAX_SAFE_INTEGER),
      bounds_mm: z.tuple([vector, vector]),
    })
    .passthrough();
  const identities = new Set();
  const cases = header.data.cases.map((input) => {
    const decoded = caseSchema.safeParse(input);
    if (!decoded.success || identities.has(decoded.data.case_id))
      throw new Error("invalid_manifest_case");
    identities.add(decoded.data.case_id);
    return decoded.data;
  });
  return { ...header.data, cases };
}

export function compareCaptures(reference, candidate) {
  const a = Buffer.from(reference.capture.rgba_base64, "base64");
  const b = Buffer.from(candidate.capture.rgba_base64, "base64");
  if (a.length !== b.length) throw new Error("capture_dimensions_mismatch");
  let different = 0;
  let maximum = 0;
  let total = 0;
  let foregroundMismatch = 0;
  for (let index = 0; index < a.length; index++) {
    const difference = Math.abs(a[index] - b[index]);
    if (difference) different++;
    maximum = Math.max(maximum, difference);
    total += difference;
    if (index % 4 === 3 && a[index] > 0 !== b[index] > 0) foregroundMismatch++;
  }
  // Position-only cyclic keys prevent tiny normal changes from selecting
  // another facet. Signed zero is numerically identical but remains in raw SHA.
  const positionTolerance = QUALITY_POLICY.position_tolerance_mm;
  const normalTolerance = QUALITY_POLICY.normal_tolerance;
  const positionComponents = [0, 1, 2, 6, 7, 8, 12, 13, 14];
  const normalComponents = [3, 4, 5, 9, 10, 11, 15, 16, 17];
  const comparePositions = (a, b) => {
    for (const index of positionComponents) {
      const delta =
        Math.round(a[index] / positionTolerance) - Math.round(b[index] / positionTolerance);
      if (delta) return delta;
    }
    return 0;
  };
  const decode = (hex) => {
    const bytes = Buffer.from(hex, "hex");
    if (bytes.length % 72) throw new Error("invalid_expanded_triangle_records");
    const triangles = [];
    for (let offset = 0; offset < bytes.length; offset += 72) {
      const corners = Array.from({ length: 3 }, (_value, corner) =>
        Array.from({ length: 6 }, (_entry, component) =>
          bytes.readFloatBE(offset + corner * 24 + component * 4),
        ),
      );
      const rotations = corners.map((_value, index) =>
        [...corners.slice(index), ...corners.slice(0, index)].flat(),
      );
      rotations.sort(comparePositions);
      const triangle = rotations[0];
      if (!triangle.every(Number.isFinite)) throw new Error("nonfinite_expanded_triangle");
      triangles.push(triangle);
    }
    return triangles;
  };
  const referenceTriangles = decode(reference.capture.position_normal_hex);
  const candidateTriangles = decode(candidate.capture.position_normal_hex);
  if (referenceTriangles.length !== candidateTriangles.length)
    throw new Error("expanded_geometry_size_mismatch");
  const keyOf = (triangle) =>
    JSON.stringify(
      positionComponents.map((index) => Math.round(triangle[index] / positionTolerance)),
    );
  const candidateGroups = new Map();
  const centroidGroups = new Map();
  const maxCandidateChecks = 4096;
  const maxPairingMs = 15000;
  const pairingStarted = performance.now();
  const cellOf = (triangle) =>
    [0, 1, 2].map((axis) =>
      Math.floor(
        (triangle[axis] + triangle[axis + 6] + triangle[axis + 12]) / (3 * positionTolerance),
      ),
    );
  const cellKey = (cell) => cell.join(",");
  for (const [id, triangle] of candidateTriangles.entries()) {
    const key = keyOf(triangle);
    const centroidKey = cellKey(cellOf(triangle));
    const entry = { id, triangle, key, centroidKey };
    const group = candidateGroups.get(key) ?? new Set();
    group.add(entry);
    candidateGroups.set(key, group);
    const spatial = centroidGroups.get(centroidKey) ?? new Set();
    spatial.add(entry);
    centroidGroups.set(centroidKey, spatial);
  }
  let maximumPositionDelta = 0;
  let maximumNormalDelta = 0;
  let matchedTriangles = 0;
  let neighborMatchedTriangles = 0;
  let candidateChecks = 0;
  const unmatchedReference = [];
  const recordDelta = (a, b) => {
    for (const index of positionComponents)
      maximumPositionDelta = Math.max(maximumPositionDelta, Math.abs(a[index] - b[index]));
    for (const index of normalComponents)
      maximumNormalDelta = Math.max(maximumNormalDelta, Math.abs(a[index] - b[index]));
  };
  for (const [referenceIndex, triangle] of referenceTriangles.entries()) {
    if (referenceIndex % 64 === 0 && performance.now() - pairingStarted > maxPairingMs)
      throw new Error(`geometry_pairing_time_limit:${maxPairingMs}ms`);
    let selected;
    let checks = 0;
    const seen = new Set();
    const consider = (entry) => {
      if (seen.has(entry.id)) return;
      seen.add(entry.id);
      if (++checks > maxCandidateChecks)
        throw new Error(`geometry_pairing_candidate_limit:${maxCandidateChecks}`);
      candidateChecks++;
      // Three cyclic rotations preserve oriented faces. Never try reversal.
      for (let rotation = 0; rotation < 3; rotation++) {
        const offset = rotation * 6;
        const oriented = [...entry.triangle.slice(offset), ...entry.triangle.slice(0, offset)];
        const positionError = Math.max(
          ...positionComponents.map((component) =>
            Math.abs(triangle[component] - oriented[component]),
          ),
        );
        if (positionError > positionTolerance) continue;
        const normalError = Math.max(
          ...normalComponents.map((component) =>
            Math.abs(triangle[component] - oriented[component]),
          ),
        );
        if (
          !selected ||
          normalError < selected.normalError ||
          (normalError === selected.normalError && positionError < selected.positionError)
        )
          selected = { entry, oriented, normalError, positionError };
      }
    };
    const exact = candidateGroups.get(keyOf(triangle));
    for (const entry of exact ?? []) {
      consider(entry);
      if (selected?.normalError === 0 && selected.positionError === 0) break;
    }
    let usedNeighbor = false;
    if (!selected || selected.normalError > normalTolerance) {
      const cell = cellOf(triangle);
      // A face whose every coordinate differs by at most one tolerance has a
      // centroid at most one tolerance away: these27 cells suffice. Each candidate
      // still needs all nine oriented positions checked before its normals matter.
      for (let x = -1; x <= 1; x++)
        for (let y = -1; y <= 1; y++)
          for (let z = -1; z <= 1; z++) {
            const group = centroidGroups.get(cellKey([cell[0] + x, cell[1] + y, cell[2] + z]));
            for (const entry of group ?? []) consider(entry);
          }
      usedNeighbor = selected !== undefined && !exact?.has(selected.entry);
    }
    if (!selected) {
      unmatchedReference.push(triangle);
      continue;
    }
    candidateGroups.get(selected.entry.key).delete(selected.entry);
    centroidGroups.get(selected.entry.centroidKey).delete(selected.entry);
    recordDelta(triangle, selected.oriented);
    matchedTriangles++;
    if (usedNeighbor) neighborMatchedTriangles++;
  }
  const unmatchedCandidate = [...candidateGroups.values()].flatMap((group) =>
    [...group].map((entry) => entry.triangle),
  );
  // These diagnostic pairs do not establish correspondence or hide winding loss.
  unmatchedReference.sort(comparePositions);
  unmatchedCandidate.sort(comparePositions);
  for (let index = 0; index < unmatchedReference.length; index++)
    recordDelta(unmatchedReference[index], unmatchedCandidate[index]);
  const maximumBoundsDelta = Math.max(
    ...reference.source_bounds_mm
      .flat()
      .map((value, index) => Math.abs(value - candidate.source_bounds_mm.flat()[index])),
  );
  return {
    max_source_bounds_delta_mm: maximumBoundsDelta,
    max_paired_expanded_position_delta_mm: maximumPositionDelta,
    max_paired_expanded_normal_delta: maximumNormalDelta,
    position_tolerance_mm: positionTolerance,
    normal_tolerance: normalTolerance,
    matched_triangles: matchedTriangles,
    neighbor_matched_triangles: neighborMatchedTriangles,
    pairing_candidate_checks: candidateChecks,
    pairing_limits: { candidate_checks_per_face: maxCandidateChecks, elapsed_ms: maxPairingMs },
    unmatched_reference_triangles: unmatchedReference.length,
    unmatched_candidate_triangles: unmatchedCandidate.length,
    position_normal_pairs_within_tolerance:
      unmatchedReference.length === 0 &&
      maximumPositionDelta <= positionTolerance &&
      maximumNormalDelta <= normalTolerance,
    numeric_pairing_scope:
      "position-quantized cyclic keys plus27 centroid neighbors with full nine-position tolerance check; all normal components paired within same oriented geometry; deterministic multiplicity consumption with finite comparison limits; unmatched facets have sorted diagnostic deltas only; raw SHA remains bit-exact including signed zero",
    expanded_position_normal_multiset_equal:
      reference.capture.signatures === candidate.capture.signatures,
    triangle_counts_equal: reference.triangle_count === candidate.triangle_count,
    source_bounds_bit_equal:
      JSON.stringify(reference.source_bounds_mm) === JSON.stringify(candidate.source_bounds_mm),
    rgba_equal: different === 0,
    rgba_differing_components: different,
    rgba_max_difference: maximum,
    rgba_mean_absolute_difference: total / a.length,
    foreground_mask_differing_pixels: foregroundMismatch,
    both_have_foreground: reference.foreground_pixels > 0 && candidate.foreground_pixels > 0,
    scope:
      "full rendered pixels and sorted expanded f32 position/normal triangle tuples; acceptance uses the explicit report quality_policy",
  };
}

async function main() {
  const config = options(process.argv.slice(2));
  const text = await readFile(config.manifest, "utf8");
  const { z } = await import(
    pathToFileURL(path.join(config.runtime, "node_modules/zod/index.js")).href
  );
  const manifest = parseManifest(text, z);
  const { chromium } = await import(
    pathToFileURL(path.join(config.runtime, "node_modules/playwright/index.mjs")).href
  );
  await mkdir(config.output, { recursive: true });

  const report = {
    schema_version: 1,
    scope: "disposable_pilot_no_production_adoption",
    manifest_path: config.manifest,
    repeat: config.repeat,
    quality_policy: QUALITY_POLICY,
    environment: {
      node: process.version,
      platform: process.platform,
      architecture: process.arch,
      three: await packageVersion(config.runtime, "three"),
      playwright: await packageVersion(config.runtime, "playwright"),
      vite: await packageVersion(config.runtime, "vite"),
    },
    cases: [],
    failures: [],
  };
  const scratch = await mkdtemp(path.join(tmpdir(), "printstash-viewer-pilot-"));
  const bundle = path.join(scratch, "bundle");
  const buildTemporary = path.join(scratch, "build-temporary");
  const browserTemporary = path.join(scratch, "browser-temporary");
  await mkdir(buildTemporary);
  await mkdir(browserTemporary);
  const originalTemporary = process.env.TMPDIR;
  const reportFile = path.join(config.output, "report.json");
  const persist = async () => {
    const pending = reportFile + ".pending";
    try {
      await writeFile(pending, JSON.stringify(report, null, 2) + "\n");
      await rename(pending, reportFile);
    } finally {
      await rm(pending, { force: true });
    }
  };
  let server;
  let browser;
  let browserServer;
  let buildChild;
  let buildExit;
  let launchPromise;
  let cleaning = false;
  let buildClosed = false;
  let expired = false;
  const stopBuild = () => {
    if (buildChild && !buildClosed) {
      try {
        process.kill(-buildChild.pid, "SIGKILL");
      } catch (error) {
        if (error.code !== "ESRCH")
          report.failures.push({ scope: "cleanup", reason: `build_stop:${error.message}` });
      }
    }
  };
  const cleanupBounded = async (promise, limit = 5000) => {
    let timer;
    try {
      return await Promise.race([
        promise,
        new Promise((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error("pilot_cleanup_timeout")), limit);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
  const deadline = Date.now() + config.timeout;
  const timeout = setTimeout(() => {
    expired = true;
    // A stuck page evaluation must not keep the parent alive indefinitely.
    stopBuild();
    browserServer?.process()?.kill("SIGKILL");
    server?.closeAllConnections();
  }, config.timeout);
  const bounded = async (promise, limit = 30000) => {
    const remaining = Math.min(limit, deadline - Date.now());
    if (remaining <= 0 || expired) throw new Error("pilot_deadline_exceeded");
    let timer;
    try {
      return await Promise.race([
        promise,
        new Promise((_resolve, reject) => {
          timer = setTimeout(() => reject(new Error("pilot_operation_timeout")), remaining);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
  try {
    await persist();
    // A timed-out in-process Vite build can recreate its outDir after cleanup.
    // Give this owned build a process boundary so it can be stopped and reaped.
    const buildOptions = {
      configFile: false,
      root: path.join(scriptDirectory, "viewer-representation-pilot"),
      logLevel: "error",
      resolve: {
        alias: [
          {
            find: "three/addons",
            replacement: path.join(config.runtime, "node_modules/three/examples/jsm"),
          },
          {
            find: "three",
            replacement: path.join(config.runtime, "node_modules/three/build/three.module.js"),
          },
          { find: "@", replacement: path.join(frontendDirectory, "src") },
        ],
      },
      build: { outDir: bundle, emptyOutDir: true, minify: true },
    };
    const viteModule = pathToFileURL(
      path.join(config.runtime, "node_modules/vite/dist/node/index.js"),
    ).href;
    buildChild = spawn(
      process.execPath,
      [
        "--input-type=module",
        "-e",
        `const {build}=await import(${JSON.stringify(viteModule)});await build(${JSON.stringify(buildOptions)});`,
      ],
      {
        detached: true,
        env: { ...process.env, TMPDIR: buildTemporary },
        stdio: ["ignore", "ignore", "inherit"],
      },
    );
    buildExit = new Promise((resolve, reject) => {
      buildChild.once("error", reject);
      buildChild.once("close", (code, signal) => {
        buildClosed = true;
        if (code === 0) resolve();
        else
          reject(
            new Error(expired ? "pilot_deadline_exceeded" : `pilot_build_failed:${signal ?? code}`),
          );
      });
    });
    await bounded(buildExit, 90000);
    server = http.createServer(async (request, response) => {
      try {
        const url = new URL(request.url, "http://localhost");
        const match = /^\/corpus\/(\d+)\/(reference|candidate)$/.exec(url.pathname);
        let filename;
        if (match) {
          const item = manifest.cases[Number(match[1])];
          if (!item) throw new Error("unknown_case");
          filename = item[match[2] === "reference" ? "reference_path" : "candidate_path"];
        } else {
          filename = path.resolve(
            bundle,
            "." + (url.pathname === "/" ? "/index.html" : decodeURIComponent(url.pathname)),
          );
          if (!filename.startsWith(bundle + path.sep)) throw new Error("invalid_path");
        }
        const size = (await stat(filename)).size;
        response.writeHead(200, {
          "content-type": filename.endsWith(".html")
            ? "text/html"
            : filename.endsWith(".js")
              ? "text/javascript"
              : "application/octet-stream",
          "content-length": size,
          "cache-control": "no-store",
        });
        const stream = createReadStream(filename);
        response.on("close", () => stream.destroy());
        stream.on("error", () => response.destroy());
        stream.pipe(response);
      } catch {
        response.writeHead(404);
        response.end();
      }
    });
    await bounded(
      new Promise((resolve, reject) => {
        server.once("error", reject);
        server.listen(0, "127.0.0.1", resolve);
      }),
    );
    const base = `http://127.0.0.1:${server.address().port}`;
    const launchTimeout = Math.min(30000, deadline - Date.now());
    if (launchTimeout <= 0 || expired) throw new Error("pilot_deadline_exceeded");
    // Playwright itself creates profile/artifact directories in its parent TMPDIR;
    // Chromium also uses TMPDIR directly. Both belong to this one scratch root.
    process.env.TMPDIR = browserTemporary;
    launchPromise = chromium.launchServer({
      headless: true,
      timeout: launchTimeout,
      env: { ...process.env, TMPDIR: browserTemporary },
    });
    // Even a handle delivered at the timeout boundary remains owned by cleanup.
    launchPromise.then(
      (owned) => {
        if (cleaning || expired) owned.process()?.kill("SIGKILL");
      },
      () => {},
    );
    browserServer = await bounded(launchPromise);
    browser = await bounded(chromium.connect(browserServer.wsEndpoint()));
    report.environment.chromium = browser.version();
    for (const [index, item] of manifest.cases.entries()) {
      const result = {
        ...item,
        reference_sha256: await hashFile(item.reference_path),
        candidate_sha256: await hashFile(item.candidate_path),
        reference_bytes: (await stat(item.reference_path)).size,
        candidate_bytes: (await stat(item.candidate_path)).size,
        observations: [],
        quality: null,
      };
      report.cases.push(result);
      const captures = new Map();
      for (let trial = 0; trial < config.repeat; trial++) {
        // Alternate order so a consistently first representation cannot win startup/cache effects.
        for (const representation of trial % 2
          ? ["candidate", "reference"]
          : ["reference", "candidate"]) {
          let context;
          const observation = { trial, representation, outcome: "failed" };
          result.observations.push(observation);
          try {
            context = await bounded(
              browser.newContext({
                viewport: { width: 640, height: 480 },
                deviceScaleFactor: 1,
                serviceWorkers: "block",
              }),
            );
            const page = await context.newPage();
            const errors = [];
            page.on("pageerror", (error) => errors.push(error.message));
            await bounded(page.goto(base, { waitUntil: "load", timeout: 15000 }));
            await bounded(
              page.waitForFunction(
                () => window.viewerRepresentationPilot !== undefined,
                undefined,
                {
                  timeout: 15000,
                },
              ),
            );
            const measured = await bounded(
              page.evaluate((input) => window.viewerRepresentationPilot.run(input), {
                url: `${base}/corpus/${index}/${representation}`,
                format: representation === "reference" ? "stl" : "glb",
                bounds_mm: item.bounds_mm,
                triangle_count: item.triangle_count,
                candidate_to_source:
                  representation === "candidate" ? item.candidate_to_source : undefined,
                comparison: item.comparison,
                capture: trial === 0,
              }),
            );
            if (errors.length) throw new Error(`browser_page_error:${errors.join(";")}`);
            if (trial === 0) {
              captures.set(representation, measured);
              await bounded(
                page.screenshot({
                  path: path.join(config.output, `${index}-${representation}.png`),
                  timeout: 15000,
                }),
              );
            }
            const { capture: _capture, ...metrics } = measured;
            Object.assign(observation, metrics);
          } catch (error) {
            observation.reason = error instanceof Error ? error.message : String(error);
            report.failures.push({
              case_id: item.case_id,
              trial,
              representation,
              reason: observation.reason,
            });
          } finally {
            if (context)
              await cleanupBounded(context.close()).catch((error) => {
                report.failures.push({
                  case_id: item.case_id,
                  trial,
                  representation,
                  scope: "cleanup",
                  reason: error.message,
                });
              });
          }
          await persist();
          if (expired || Date.now() >= deadline) throw new Error("pilot_deadline_exceeded");
        }
      }
      if (captures.has("reference") && captures.has("candidate")) {
        try {
          result.quality = compareCaptures(captures.get("reference"), captures.get("candidate"));
          const checks = {
            oriented_position_normal_pairs: result.quality.position_normal_pairs_within_tolerance,
            source_bounds:
              result.quality.max_source_bounds_delta_mm <=
              QUALITY_POLICY.source_bounds_tolerance_mm,
            foreground_nonempty: result.quality.both_have_foreground,
            foreground_mask:
              result.quality.foreground_mask_differing_pixels <=
              QUALITY_POLICY.foreground_mask_max_differing_pixels,
            rgba_components:
              result.quality.rgba_max_difference <= QUALITY_POLICY.rgba_max_component_difference,
          };
          result.quality.policy_checks = checks;
          result.quality.accepted = Object.values(checks).every((passed) => passed);
          if (!checks.foreground_nonempty)
            report.failures.push({
              case_id: item.case_id,
              scope: "quality",
              reason: "empty_render",
            });
          else if (!result.quality.accepted)
            report.failures.push({
              case_id: item.case_id,
              scope: "quality",
              reason: "representation_quality",
              failed_checks: Object.entries(checks)
                .filter(([_name, passed]) => !passed)
                .map(([name]) => name),
            });
        } catch (error) {
          result.quality = {
            outcome: "failed",
            reason: error instanceof Error ? error.message : String(error),
          };
          report.failures.push({
            case_id: item.case_id,
            scope: "quality_comparison",
            reason: result.quality.reason,
          });
        }
      }
      await persist();
    }
  } catch (error) {
    report.failures.push({
      scope: "harness",
      reason: error instanceof Error ? error.message : String(error),
    });
  } finally {
    cleaning = true;
    clearTimeout(timeout);
    stopBuild();
    const cleanupError = (error) =>
      report.failures.push({ scope: "cleanup", reason: error.message });
    if (buildExit) await cleanupBounded(buildExit.catch(() => {})).catch(cleanupError);
    if (launchPromise && !browserServer)
      browserServer = await cleanupBounded(launchPromise.catch(() => undefined)).catch((error) => {
        cleanupError(error);
        return undefined;
      });
    const closing = setTimeout(() => browserServer?.process()?.kill("SIGKILL"), 5000);
    try {
      if (browser) await cleanupBounded(browser.close()).catch(cleanupError);
      if (browserServer) await cleanupBounded(browserServer.close()).catch(cleanupError);
    } finally {
      clearTimeout(closing);
    }
    if (server) {
      server.closeAllConnections();
      await cleanupBounded(new Promise((resolve) => server.close(resolve))).catch(cleanupError);
    }
    if (originalTemporary === undefined) delete process.env.TMPDIR;
    else process.env.TMPDIR = originalTemporary;
    // This root alone contains our bundle and both processes scratch files.
    await rm(scratch, { recursive: true, force: true });
    await persist();
  }
  console.log(
    JSON.stringify({
      report: reportFile,
      cases: report.cases.length,
      observations: report.cases.reduce((sum, item) => sum + item.observations.length, 0),
      failures: report.failures.length,
    }),
  );
  if (report.failures.length) process.exitCode = 1;
}
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
  });
}
