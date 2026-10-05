/** Public CLI safety contracts for the disposable browser representation pilot. */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, readdir, writeFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { describe, test } from "node:test";
import { compareCaptures } from "./viewer-representation-pilot.mjs";

const cli = fileURLToPath(new URL("./viewer-representation-pilot.mjs", import.meta.url));
const runtime =
  process.env.VIEWER_PILOT_RUNTIME_DIR ??
  fileURLToPath(new URL("./viewer-representation-pilot/", import.meta.url));

async function arrange(t) {
  const directory = await mkdtemp(path.join(tmpdir(), "viewer-pilot-cli-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const temporary = path.join(directory, "owned-temporary");
  await mkdir(temporary);
  return {
    directory,
    temporary,
    manifest: path.join(directory, "manifest.json"),
    output: path.join(directory, "output"),
  };
}

function runCli(fixture, extra = []) {
  return new Promise((resolve, reject) => {
    const child = spawn(
      process.execPath,
      [
        cli,
        "--runtime-dir",
        runtime,
        "--manifest",
        fixture.manifest,
        "--output",
        fixture.output,
        "--repeat",
        "1",
        ...extra,
      ],
      { env: { ...process.env, TMPDIR: fixture.temporary }, stdio: ["ignore", "pipe", "pipe"] },
    );
    let stdout = "";
    let stderr = "";
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    const timer = setTimeout(() => {
      child.kill("SIGKILL");
      reject(new Error("CLI exceeded the test containment deadline"));
    }, 60000);
    child.once("error", (error) => {
      clearTimeout(timer);
      reject(error);
    });
    child.once("exit", (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, stdout, stderr });
    });
  });
}

async function collinearCase(fixture) {
  // Positive vertex bounds but zero triangle area: both loaders genuinely render no foreground.
  const vertices = [0, 0, 0, 1, 0, 0, 2, 0, 0];
  const normal = [0, 0, 1, 0, 0, 1, 0, 0, 1];
  const stl = Buffer.alloc(134);
  stl.writeUInt32LE(1, 80);
  [...normal.slice(0, 3), ...vertices].forEach((value, index) =>
    stl.writeFloatLE(value, 84 + index * 4),
  );
  const data = Buffer.alloc(80);
  vertices.forEach((value, index) => data.writeFloatLE(value, index * 4));
  normal.forEach((value, index) => data.writeFloatLE(value, 36 + index * 4));
  [0, 1, 2].forEach((value, index) => data.writeUInt16LE(value, 72 + index * 2));
  const document = {
    asset: { version: "2.0" },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0 }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 }, indices: 2 }] }],
    buffers: [{ byteLength: data.length }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: 36 },
      { buffer: 0, byteOffset: 36, byteLength: 36 },
      { buffer: 0, byteOffset: 72, byteLength: 6 },
    ],
    accessors: [
      {
        bufferView: 0,
        componentType: 5126,
        count: 3,
        type: "VEC3",
        min: [0, 0, 0],
        max: [2, 0, 0],
      },
      { bufferView: 1, componentType: 5126, count: 3, type: "VEC3" },
      { bufferView: 2, componentType: 5123, count: 3, type: "SCALAR" },
    ],
  };
  const json = Buffer.from(JSON.stringify(document));
  const padded = Buffer.alloc(Math.ceil(json.length / 4) * 4, 32);
  json.copy(padded);
  const glb = Buffer.alloc(12 + 8 + padded.length + 8 + data.length);
  glb.writeUInt32LE(0x46546c67, 0);
  glb.writeUInt32LE(2, 4);
  glb.writeUInt32LE(glb.length, 8);
  glb.writeUInt32LE(padded.length, 12);
  glb.writeUInt32LE(0x4e4f534a, 16);
  padded.copy(glb, 20);
  const binaryOffset = 20 + padded.length;
  glb.writeUInt32LE(data.length, binaryOffset);
  glb.writeUInt32LE(0x004e4942, binaryOffset + 4);
  data.copy(glb, binaryOffset + 8);
  const reference = path.join(fixture.directory, "reference.stl");
  const candidate = path.join(fixture.directory, "candidate.glb");
  await writeFile(reference, stl);
  await writeFile(candidate, glb);
  return {
    schema_version: 1,
    cases: [
      {
        case_id: "collinear",
        source_sha256: createHash("sha256").update(stl).digest("hex"),
        reference_path: reference,
        candidate_path: candidate,
        triangle_count: 1,
        bounds_mm: [
          [0, 0, 0],
          [2, 0, 0],
        ],
      },
    ],
  };
}

describe("viewer representation pilot CLI", { concurrency: false }, () => {
  test("rejects corrupted manifest JSON", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, "{broken");
    const result = await runCli(fixture);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /SyntaxError/);
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
  test("rejects an unsupported manifest version", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, JSON.stringify({ schema_version: 2, cases: [] }));
    const result = await runCli(fixture);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /invalid_manifest_v1/);
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
  test("rejects non-string manifest identities", async (t) => {
    const fixture = await arrange(t);
    const manifest = await collinearCase(fixture);
    for (const identity of [7, null, ["collinear"]]) {
      manifest.cases[0].case_id = identity;
      await writeFile(fixture.manifest, JSON.stringify(manifest));
      const result = await runCli(fixture);
      assert.equal(result.code, 1);
      assert.match(result.stderr, /invalid_manifest_case/);
      assert.deepEqual(await readdir(fixture.temporary), []);
    }
  });
  test("rejects a nonexistent manifest", async (t) => {
    const fixture = await arrange(t);
    const result = await runCli(fixture);
    assert.equal(result.code, 1);
    assert.match(result.stderr, /ENOENT/);
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
  test("preserves a nonexistent representation failure", async (t) => {
    const fixture = await arrange(t);
    const manifest = await collinearCase(fixture);
    manifest.cases[0].candidate_path = path.join(fixture.directory, "missing.glb");
    await writeFile(fixture.manifest, JSON.stringify(manifest));
    const result = await runCli(fixture);
    assert.equal(result.code, 1);
    const report = JSON.parse(await readFile(path.join(fixture.output, "report.json"), "utf8"));
    assert.ok(
      report.failures.some((failure) => failure.reason.includes("ENOENT")),
      JSON.stringify(report.failures),
    );
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
  test("preserves deadline failure while releasing its temporary bundle", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, JSON.stringify(await collinearCase(fixture)));
    const result = await runCli(fixture, ["--timeout-ms", "1000"]);
    const diagnostics = JSON.stringify(result);
    assert.equal(result.code, 1, diagnostics);
    assert.equal(result.signal, null, diagnostics);
    const encoded = await readFile(path.join(fixture.output, "report.json"), "utf8");
    assert.ok(encoded.length > 0, diagnostics);
    const report = JSON.parse(encoded);
    assert.ok(
      report.failures.some((failure) => /timeout|deadline|closed/i.test(failure.reason)),
      JSON.stringify(report.failures),
    );
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
  test("rejects two empty renders while preserving their observations", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, JSON.stringify(await collinearCase(fixture)));
    const result = await runCli(fixture);
    const report = JSON.parse(await readFile(path.join(fixture.output, "report.json"), "utf8"));
    assert.equal(report.cases.length, 1);
    assert.equal(report.cases[0].observations.length, 2);
    assert.ok(
      report.cases[0].observations.every((item) => item.outcome === "success"),
      JSON.stringify(report.failures),
    );
    assert.equal(report.cases[0].quality.both_have_foreground, false);
    assert.equal(report.cases[0].quality.rgba_equal, true);
    assert.equal(result.code, 1, "equal blank images cannot qualify a representation");
    assert.ok(
      report.failures.some(
        (failure) => failure.case_id === "collinear" && failure.reason === "empty_render",
      ),
    );
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
});

function geometryCapture(triangles) {
  const words = triangles
    .map((corners) => {
      const rotations = corners.map((_corner, index) => [
        ...corners.slice(index),
        ...corners.slice(0, index),
      ]);
      return rotations
        .map((rotation) => {
          const values = rotation.flat();
          const buffer = Buffer.alloc(values.length * 4);
          values.forEach((value, index) => buffer.writeFloatBE(value, index * 4));
          return buffer.toString("hex");
        })
        .sort()[0];
    })
    .sort()
    .join("");
  return {
    capture: {
      position_normal_hex: words,
      signatures: createHash("sha256").update(words).digest("hex"),
      rgba_base64: Buffer.from([1, 2, 3, 255]).toString("base64"),
    },
    triangle_count: triangles.length,
    source_bounds_mm: [
      [0, 0, 0],
      [4, 4, 0],
    ],
    foreground_pixels: 1,
  };
}
const corner = (x, y, z, nx = 0, ny = 0, nz = 1) => [x, y, z, nx, ny, nz];

describe("viewer geometry comparison", () => {
  test("pairs facets by positions before perturbed normals", () => {
    const reference = geometryCapture([
      [corner(0, 0, 0, 1e-8), corner(1, 0, 0, 1e-8), corner(0, 1, 0, 1e-8)],
      [corner(0, 0, 0, -1e-8), corner(4, 0, 0, -1e-8), corner(0, 4, 0, -1e-8)],
    ]);
    const candidate = geometryCapture([
      [corner(0, 0, 0, -1e-8), corner(1, 0, 0, -1e-8), corner(0, 1, 0, -1e-8)],
      [corner(0, 0, 0, 1e-8), corner(4, 0, 0, 1e-8), corner(0, 4, 0, 1e-8)],
    ]);
    const quality = compareCaptures(reference, candidate);
    assert.equal(quality.expanded_position_normal_multiset_equal, false);
    assert.equal(quality.max_paired_expanded_position_delta_mm, 0);
    assert.ok(quality.max_paired_expanded_normal_delta < 3e-8);
  });
  test("matches signed zero coordinates numerically", () => {
    const reference = geometryCapture([[corner(-0, -0, 0), corner(1, 0, 0), corner(0, 1, 0)]]);
    const candidate = geometryCapture([[corner(0, 0, 0), corner(1, 0, 0), corner(0, 1, 0)]]);
    const quality = compareCaptures(reference, candidate);
    assert.equal(quality.max_paired_expanded_position_delta_mm, 0);
    assert.equal(quality.max_paired_expanded_normal_delta, 0);
    assert.equal(quality.expanded_position_normal_multiset_equal, false);
  });
  test("matches a face across a quantization boundary", () => {
    const reference = geometryCapture([
      [corner(0.00000499, 0, 0), corner(1, 0, 0), corner(0, 1, 0)],
    ]);
    const candidate = geometryCapture([
      [corner(0.00000501, 0, 0), corner(1, 0, 0), corner(0, 1, 0)],
    ]);
    const quality = compareCaptures(reference, candidate);
    const context = JSON.stringify(quality);
    assert.equal(quality.expanded_position_normal_multiset_equal, false, context);
    assert.ok(quality.max_paired_expanded_position_delta_mm < 1e-5, context);
    assert.equal(quality.max_paired_expanded_normal_delta, 0, context);
    assert.equal(quality.position_normal_pairs_within_tolerance, true, context);
    assert.equal(quality.matched_triangles, 1, context);
    assert.equal(quality.unmatched_reference_triangles, 0, context);
    assert.equal(quality.unmatched_candidate_triangles, 0, context);
  });
  test("reports genuine coordinate displacement", () => {
    const reference = geometryCapture([[corner(0, 0, 0), corner(1, 0, 0), corner(0, 1, 0)]]);
    const candidate = geometryCapture([[corner(1, 0, 0), corner(2, 0, 0), corner(1, 1, 0)]]);
    const quality = compareCaptures(reference, candidate);
    assert.equal(quality.expanded_position_normal_multiset_equal, false);
    assert.ok(quality.max_paired_expanded_position_delta_mm >= 1);
  });
  test("keeps reversed face winding distinguishable", () => {
    const a = corner(0, 0, 0),
      b = corner(1, 0, 0),
      c = corner(0, 1, 0);
    const quality = compareCaptures(geometryCapture([[a, b, c]]), geometryCapture([[a, c, b]]));
    assert.equal(quality.expanded_position_normal_multiset_equal, false);
    assert.ok(quality.max_paired_expanded_position_delta_mm > 0);
  });
});

async function placementCase(fixture, kind) {
  const manifest = await collinearCase(fixture);
  const item = manifest.cases[0];
  const original = await readFile(item.candidate_path);
  const jsonLength = original.readUInt32LE(12);
  const document = JSON.parse(original.subarray(20, 20 + jsonLength).toString());
  const data = Buffer.alloc(kind === "instances" ? 104 : 80);
  original.subarray(28 + jsonLength).copy(data);
  const vertices = [0, 0, 0, 1, 0, 0, 0, 1, 0];
  vertices.forEach((value, index) => data.writeFloatLE(value, index * 4));
  document.accessors[0].max = [1, 1, 0];
  if (kind === "instances") {
    [0, 0, 0, 3, 0, 0].forEach((value, index) => data.writeFloatLE(value, 80 + index * 4));
    document.bufferViews.push({ buffer: 0, byteOffset: 80, byteLength: 24 });
    document.accessors.push({ bufferView: 3, componentType: 5126, count: 2, type: "VEC3" });
    document.nodes[0].extensions = { EXT_mesh_gpu_instancing: { attributes: { TRANSLATION: 3 } } };
    document.extensionsUsed = ["EXT_mesh_gpu_instancing"];
    document.extensionsRequired = ["EXT_mesh_gpu_instancing"];
  } else if (kind === "shear") {
    // Prototype counterexample: x += 0.5*y is valid source shear, but this
    // column-major node matrix violates glTF decomposable-TRS requirements.
    document.nodes[0].matrix = [1, 0, 0, 0, 0.5, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
  } else document.nodes[0].scale = [-1, 1, 1];
  document.buffers[0].byteLength = data.length;
  const encoded = Buffer.from(JSON.stringify(document));
  const json = Buffer.alloc(Math.ceil(encoded.length / 4) * 4, 32);
  encoded.copy(json);
  const output = Buffer.alloc(12 + 8 + json.length + 8 + data.length);
  output.writeUInt32LE(0x46546c67, 0);
  output.writeUInt32LE(2, 4);
  output.writeUInt32LE(output.length, 8);
  output.writeUInt32LE(json.length, 12);
  output.writeUInt32LE(0x4e4f534a, 16);
  json.copy(output, 20);
  output.writeUInt32LE(data.length, 20 + json.length);
  output.writeUInt32LE(0x004e4942, 24 + json.length);
  data.copy(output, 28 + json.length);
  await writeFile(item.candidate_path, output);
  const faces =
    kind === "instances"
      ? [vertices, [3, 0, 0, 4, 0, 0, 3, 1, 0]]
      : kind === "shear"
        ? [[0, 0, 0, 1, 0, 0, 0.5, 1, 0]]
        : [[0, 0, 0, 0, 1, 0, -1, 0, 0]];
  const stl = Buffer.alloc(84 + 50 * faces.length);
  stl.writeUInt32LE(faces.length, 80);
  faces.forEach((face, index) =>
    [0, 0, 1, ...face].forEach((value, component) =>
      stl.writeFloatLE(value, 84 + 50 * index + 4 * component),
    ),
  );
  await writeFile(item.reference_path, stl);
  item.case_id = kind;
  item.source_sha256 = createHash("sha256").update(stl).digest("hex");
  item.triangle_count = faces.length;
  item.bounds_mm =
    kind === "instances"
      ? [
          [0, 0, 0],
          [4, 1, 0],
        ]
      : kind === "shear"
        ? [
            [0, 0, 0],
            [1, 1, 0],
          ]
        : [
            [-1, 0, 0],
            [0, 1, 0],
          ];
  return manifest;
}

describe("viewer placement comparison", { concurrency: false }, () => {
  test("preserves effective winding under a reflected placement", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, JSON.stringify(await placementCase(fixture, "reflection")));
    const result = await runCli(fixture);
    const report = JSON.parse(await readFile(path.join(fixture.output, "report.json"), "utf8"));
    assert.equal(result.code, 0, JSON.stringify(report.failures));
    const quality = report.cases[0].quality;
    const context = JSON.stringify(quality);
    // Inverse-transpose reflection can retain -0 in raw normals. Require the
    // rendered facing and every numerical component, not identical zero bits.
    assert.equal(quality.both_have_foreground, true, context);
    assert.equal(quality.position_normal_pairs_within_tolerance, true, context);
    assert.equal(quality.matched_triangles, report.cases[0].triangle_count, context);
    assert.equal(quality.unmatched_reference_triangles, 0, context);
    assert.equal(quality.unmatched_candidate_triangles, 0, context);
    assert.equal(quality.max_paired_expanded_position_delta_mm, 0, context);
    assert.equal(quality.max_paired_expanded_normal_delta, 0, context);
    assert.equal(quality.foreground_mask_differing_pixels, 0, context);
    assert.equal(quality.rgba_equal, true, context);
  });
  test("rejects a sheared representation with lost geometry", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, JSON.stringify(await placementCase(fixture, "shear")));
    const result = await runCli(fixture);
    const report = JSON.parse(await readFile(path.join(fixture.output, "report.json"), "utf8"));
    const observed = report.cases[0];
    const context = JSON.stringify(observed.quality);
    assert.equal(observed.observations.length, 2);
    assert.ok(
      observed.observations.every((item) => item.outcome === "success"),
      JSON.stringify(report.failures),
    );
    assert.equal(observed.quality.both_have_foreground, true, context);
    assert.equal(observed.quality.position_normal_pairs_within_tolerance, false, context);
    assert.ok(observed.quality.max_paired_expanded_position_delta_mm > 1e-5, context);
    assert.ok(observed.quality.foreground_mask_differing_pixels > 0, context);
    assert.ok(observed.quality.rgba_max_difference > 1, context);
    assert.equal(
      result.code,
      1,
      "successful loaders must not certify changed representation geometry",
    );
    assert.ok(
      report.failures.some(
        (failure) => failure.case_id === "shear" && failure.reason === "representation_quality",
      ),
      JSON.stringify(report.failures),
    );
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
  test("counts actual instanced placements in geometry and memory", async (t) => {
    const fixture = await arrange(t);
    await writeFile(fixture.manifest, JSON.stringify(await placementCase(fixture, "instances")));
    const result = await runCli(fixture);
    const report = JSON.parse(await readFile(path.join(fixture.output, "report.json"), "utf8"));
    assert.equal(result.code, 0, JSON.stringify(report.failures));
    assert.equal(report.cases[0].quality.expanded_position_normal_multiset_equal, true);
    const candidate = report.cases[0].observations.find(
      (item) => item.representation === "candidate",
    );
    assert.equal(candidate.triangle_count, 2);
    // Positions36 + normals36 + uint16 indices6 + two actual mat4 transforms128.
    assert.equal(candidate.cpu.geometry_unique_attribute_range_bytes, 206);
    assert.equal(candidate.gpu.draw_calls, 1);
    assert.equal(candidate.gpu.submitted_triangles, 2);
    assert.deepEqual(await readdir(fixture.temporary), []);
  });
});

describe("viewer WebGL storage accounting", { concurrency: false }, () => {
  test("counts actual WebGL subrange storage", async (t) => {
    const fixture = await arrange(t);
    const { build } = await import(
      pathToFileURL(path.join(runtime, "node_modules/vite/dist/node/index.js")).href
    );
    const { chromium } = await import(
      pathToFileURL(path.join(runtime, "node_modules/playwright/index.mjs")).href
    );
    const frontend = path.resolve(path.dirname(cli), "..");
    const result = await build({
      configFile: false,
      logLevel: "error",
      resolve: {
        alias: [
          {
            find: "three/addons",
            replacement: path.join(runtime, "node_modules/three/examples/jsm"),
          },
          {
            find: "three",
            replacement: path.join(runtime, "node_modules/three/build/three.module.js"),
          },
          { find: "@", replacement: path.join(frontend, "src") },
        ],
      },
      build: {
        write: false,
        lib: {
          entry: path.join(path.dirname(cli), "viewer-representation-pilot/browser.ts"),
          name: "ViewerPilot",
          formats: ["iife"],
        },
      },
    });
    const originalTemporary = process.env.TMPDIR;
    process.env.TMPDIR = fixture.temporary;
    let browser;
    try {
      browser = await chromium.launch({ headless: true, timeout: 15000 });
      const page = await browser.newPage();
      const outputs = Array.isArray(result) ? result : [result];
      const script = outputs
        .flatMap((output) => output.output)
        .find((item) => item.type === "chunk");
      assert.ok(script, "Vite must emit the actual browser instrumentation bundle");
      await page.addScriptTag({ content: script.code });
      const observations = await page.evaluate(() => {
        const gl = document.createElement("canvas").getContext("webgl2");
        if (!gl) throw new Error("webgl2_unavailable");
        const storage = window.viewerRepresentationPilot.observeBuffers(gl);
        const buffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
        const results = [];
        const record = () =>
          results.push({
            observed: storage(),
            actual: gl.getBufferParameter(gl.ARRAY_BUFFER, gl.BUFFER_SIZE),
            error: gl.getError(),
          });
        gl.bufferData(gl.ARRAY_BUFFER, 32, gl.STATIC_DRAW);
        record();
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(8), gl.STATIC_DRAW, 2, 3);
        record();
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(8), gl.STATIC_DRAW, 2, 0);
        record();
        gl.bufferData(gl.ARRAY_BUFFER, new DataView(new ArrayBuffer(16)), gl.STATIC_DRAW, 3, 5);
        record();
        gl.bufferData(gl.ARRAY_BUFFER, new Uint8Array(7), gl.STATIC_DRAW);
        record();
        gl.deleteBuffer(buffer);
        results.push({ observed: storage(), actual: 0, error: gl.getError() });
        gl.getExtension("WEBGL_lose_context")?.loseContext();
        return results;
      });
      assert.deepEqual(
        observations.map((item) => item.actual),
        [32, 12, 24, 5, 7, 0],
      );
      assert.deepEqual(
        observations.map((item) => item.observed.bytes),
        [32, 12, 24, 5, 7, 0],
      );
      assert.deepEqual(
        observations.map((item) => item.error),
        [0, 0, 0, 0, 0, 0],
      );
      assert.equal(observations.at(-1).observed.count, 0);
    } finally {
      if (browser) await browser.close();
      if (originalTemporary === undefined) delete process.env.TMPDIR;
      else process.env.TMPDIR = originalTemporary;
    }
  });
});
