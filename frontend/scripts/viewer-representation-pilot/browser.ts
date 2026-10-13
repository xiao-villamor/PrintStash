/** Disposable corpus pilot. The product viewer and its load/cache paths stay untouched. */
import * as THREE from "three";
import { STLLoader } from "three/addons/loaders/STLLoader.js";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { comparisonTransform, sharedScale, type MeshComparison } from "@/lib/comparison-camera";
import { fitCameraToBounds } from "@/lib/thumbnail-camera";

interface Input {
  url: string;
  format: "stl" | "glb";
  bounds_mm: number[][];
  triangle_count: number;
  candidate_to_source?: number[];
  comparison?: MeshComparison;
  capture: boolean;
}

declare global {
  interface Window {
    viewerRepresentationPilot: { run: typeof run; observeBuffers: typeof observeBuffers };
  }
}

function meshes(root: THREE.Object3D): THREE.Mesh[] {
  const result: THREE.Mesh[] = [];
  root.traverse((object) => {
    if (object instanceof THREE.Mesh) result.push(object);
  });
  if (!result.length) throw new Error("representation_has_no_mesh");
  return result;
}

function cpuStorage(items: THREE.Mesh[], input: ArrayBuffer) {
  const buffers = new Set<ArrayBufferLike>();
  const ranges = new Map<ArrayBufferLike, [number, number][]>();
  for (const mesh of items) {
    const geometry = mesh.geometry;
    const attributes = [...Object.values(geometry.attributes), geometry.index];
    if (mesh instanceof THREE.InstancedMesh)
      attributes.push(mesh.instanceMatrix, mesh.instanceColor);
    for (const attribute of attributes.filter(
      (value): value is THREE.BufferAttribute | THREE.InterleavedBufferAttribute => value !== null,
    )) {
      const array =
        attribute instanceof THREE.InterleavedBufferAttribute
          ? attribute.data.array
          : attribute.array;
      buffers.add(array.buffer);
      const current = ranges.get(array.buffer) ?? [];
      current.push([array.byteOffset, array.byteOffset + array.byteLength]);
      ranges.set(array.buffer, current);
    }
  }
  let used = 0;
  for (const intervals of ranges.values()) {
    intervals.sort((a, b) => a[0] - b[0]);
    let start = 0;
    let end = 0;
    for (const [left, right] of intervals) {
      if (left > end) {
        used += end - start;
        start = left;
        end = right;
      } else end = Math.max(end, right);
    }
    used += end - start;
  }
  const geometryBacking = [...buffers].reduce((sum, buffer) => sum + buffer.byteLength, 0);
  return {
    geometry_unique_backing_bytes: geometryBacking,
    geometry_unique_attribute_range_bytes: used,
    unique_backing_buffers: buffers.size,
    retained_input_bytes: input.byteLength,
    input_shared_with_geometry: buffers.has(input),
    geometry_plus_retained_input_bytes:
      geometryBacking + (buffers.has(input) ? 0 : input.byteLength),
    scope:
      "exact retained geometry/instance/input ArrayBuffers; excludes JS objects, parser temporaries and heap peak",
  };
}

// Order-independent exact triangle multiset. Corner ordering and multiplicity are retained.
// Computed only for the first observation, outside all measured phase intervals.
function expandedSignatures(items: THREE.Mesh[]) {
  const position = new THREE.Vector3();
  const normal = new THREE.Vector3();
  const matrix = new THREE.Matrix3();
  const instance = new THREE.Matrix4();
  const world = new THREE.Matrix4();
  const records: string[] = [];
  const bits = new Float32Array(6);
  const words = new Uint32Array(bits.buffer);
  for (const mesh of items) {
    const geometry = mesh.geometry;
    const positions = geometry.getAttribute("position");
    const normals = geometry.getAttribute("normal");
    if (!positions || !normals) throw new Error("position_or_explicit_normal_missing");
    const count = geometry.index?.count ?? positions.count;
    if (count % 3) throw new Error("non_triangular_geometry");
    const placements = mesh instanceof THREE.InstancedMesh ? mesh.count : 1;
    for (let placement = 0; placement < placements; placement++) {
      if (mesh instanceof THREE.InstancedMesh) {
        mesh.getMatrixAt(placement, instance);
        world.multiplyMatrices(mesh.matrixWorld, instance);
      } else world.copy(mesh.matrixWorld);
      matrix.getNormalMatrix(world);
      const reflected = world.determinant() < 0;
      for (let face = 0; face < count; face += 3) {
        const corners: string[] = [];
        for (const corner of reflected ? [0, 2, 1] : [0, 1, 2]) {
          const index = geometry.index?.getX(face + corner) ?? face + corner;
          position.fromBufferAttribute(positions, index).applyMatrix4(world);
          normal.fromBufferAttribute(normals, index).applyMatrix3(matrix);
          bits.set([...position.toArray(), ...normal.toArray()]);
          if (![...bits].every(Number.isFinite)) throw new Error("nonfinite_geometry");
          corners.push([...words].map((word) => word.toString(16).padStart(8, "0")).join(""));
        }
        // Three flips FrontSide for a negative world determinant. Account for
        // that effective facing before cyclic ordering, never erase true winding.
        records.push(
          [
            corners.join(""),
            [corners[1], corners[2], corners[0]].join(""),
            [corners[2], corners[0], corners[1]].join(""),
          ].sort()[0],
        );
      }
    }
  }
  records.sort();
  return records;
}

function observeBuffers(gl: WebGL2RenderingContext) {
  const binding = new Map<number, WebGLBuffer | null>();
  const sizes = new Map<WebGLBuffer, number>();
  const bind = gl.bindBuffer.bind(gl);
  const data = gl.bufferData.bind(gl);
  const remove = gl.deleteBuffer.bind(gl);
  gl.bindBuffer = (target, buffer) => {
    binding.set(target, buffer);
    bind(target, buffer);
  };
  gl.bufferData = (
    target: number,
    source: number | AllowSharedBufferSource | null,
    usage: number,
    sourceOffset?: number,
    length?: number,
  ) => {
    let bytes: number;
    if (source === null) {
      data(target, source, usage);
      bytes = 0;
    } else if (ArrayBuffer.isView(source)) {
      if (sourceOffset === undefined) {
        data(target, source, usage);
        bytes = source.byteLength;
      } else {
        data(target, source, usage, sourceOffset, length);
        // WebGL offsets/lengths count typed-array elements; DataView counts bytes.
        const elementBytes =
          source instanceof DataView
            ? 1
            : Number("BYTES_PER_ELEMENT" in source ? source.BYTES_PER_ELEMENT : NaN);
        if (!Number.isSafeInteger(elementBytes) || elementBytes <= 0)
          throw new Error("invalid_buffer_data_view");
        const elements = source.byteLength / elementBytes;
        bytes =
          (length === undefined || length === 0 ? elements - sourceOffset : length) * elementBytes;
      }
    } else if (
      source instanceof ArrayBuffer ||
      ("SharedArrayBuffer" in globalThis && source instanceof globalThis.SharedArrayBuffer)
    ) {
      data(target, source, usage);
      bytes = source.byteLength;
    } else {
      // Without a shared-buffer constructor, the remaining valid overload is
      // numeric allocation. Reject an unsupported buffer instead of coercing it.
      bytes = Number(source);
      if (!Number.isSafeInteger(bytes) || bytes < 0) throw new Error("invalid_buffer_data_size");
      data(target, bytes, usage);
    }
    const buffer = binding.get(target);
    if (buffer) sizes.set(buffer, bytes);
  };
  gl.deleteBuffer = (buffer) => {
    if (buffer) sizes.delete(buffer);
    remove(buffer);
  };
  return () => ({ count: sizes.size, bytes: [...sizes.values()].reduce((a, b) => a + b, 0) });
}

async function sha256(bytes: Uint8Array<ArrayBuffer>) {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

async function run(input: Input) {
  const width = 640;
  const height = 480;
  const start = performance.now();
  const response = await fetch(input.url, { cache: "no-store" });
  if (!response.ok) throw new Error(`representation_http_${response.status}`);
  const bytes = await response.arrayBuffer();
  const downloaded = performance.now();
  const root =
    input.format === "stl"
      ? new THREE.Group().add(new THREE.Mesh(new STLLoader().parse(bytes)))
      : (await new GLTFLoader().parseAsync(bytes, location.origin + "/")).scene;
  const parsed = performance.now();
  if (input.format === "glb" && input.candidate_to_source) {
    if (
      input.candidate_to_source.length !== 16 ||
      input.candidate_to_source.some((value) => !Number.isFinite(value))
    )
      throw new Error("invalid_candidate_transform");
    root.applyMatrix4(new THREE.Matrix4().fromArray(input.candidate_to_source).transpose());
  }
  root.updateMatrixWorld(true);
  const items = meshes(root);
  const sourceBounds = new THREE.Box3().setFromObject(root);
  const triangleCount = items.reduce(
    (sum, mesh) =>
      sum +
      ((mesh.geometry.index?.count ?? mesh.geometry.getAttribute("position").count) / 3) *
        (mesh instanceof THREE.InstancedMesh ? mesh.count : 1),
    0,
  );
  if (triangleCount !== input.triangle_count) throw new Error("triangle_count_mismatch");
  const memory = cpuStorage(items, bytes);
  const material = new THREE.MeshStandardMaterial({
    color: "#8a93a6",
    roughness: 0.45,
    metalness: 0.1,
  });
  const oldMaterials = new Set<THREE.Material>();
  for (const mesh of items) {
    for (const old of Array.isArray(mesh.material) ? mesh.material : [mesh.material])
      oldMaterials.add(old);
    mesh.material = material;
  }
  const group = new THREE.Group().add(root);
  if (input.comparison) group.applyMatrix4(comparisonTransform(input.comparison));
  group.applyMatrix4(new THREE.Matrix4().makeRotationX(-Math.PI / 2));
  group.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(group);
  const size = box.getSize(new THREE.Vector3());
  const scale = input.comparison
    ? sharedScale(input.comparison.referenceSizeMm)
    : 10 / Math.max(size.x, size.y, size.z);
  if (!Number.isFinite(scale) || scale <= 0) throw new Error("invalid_viewer_scale");
  group.scale.multiplyScalar(scale);
  group.position.sub(box.getCenter(new THREE.Vector3()).multiplyScalar(scale));
  const scene = new THREE.Scene().add(group);
  scene.add(
    new THREE.AmbientLight(0xffffff, 0.5),
    new THREE.HemisphereLight("#d4e8ff", "#1a1a2e", 0.4),
  );
  for (const [position, intensity, color] of [
    [[8, 12, 6], 1.2, 0xffffff],
    [[-6, -4, -8], 0.25, 0xffffff],
    [[0, -8, 0], 0.15, "#8899bb"],
  ] as const) {
    const light = new THREE.DirectionalLight(color, intensity);
    light.position.set(position[0], position[1], position[2]);
    scene.add(light);
  }
  const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 1000);
  camera.position.copy(
    fitCameraToBounds(
      input.comparison ? new THREE.Vector3(10, 10, 10) : size.multiplyScalar(scale),
      width / height,
      camera.fov,
    ).position,
  );
  camera.lookAt(0, 0, 0);
  const canvas = document.createElement("canvas");
  const gl = canvas.getContext("webgl2", { antialias: true, alpha: true });
  if (!gl) throw new Error("webgl2_unavailable");
  const bufferStorage = observeBuffers(gl);
  const renderer = new THREE.WebGLRenderer({ canvas, context: gl, antialias: true, alpha: true });
  renderer.setPixelRatio(1);
  renderer.setSize(width, height);
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.setClearColor(0x000000, 0);
  document.body.replaceChildren(canvas);
  const prepared = performance.now();
  try {
    renderer.render(scene, camera);
    const submitted = performance.now();
    gl.finish();
    const completed = performance.now();
    const pixel = new Uint8Array(width * height * 4);
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
    const buffers = bufferStorage();
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    const environment = {
      three_revision: THREE.REVISION,
      user_agent: navigator.userAgent,
      webgl_renderer: debug
        ? gl.getParameter(debug.UNMASKED_RENDERER_WEBGL)
        : gl.getParameter(gl.RENDERER),
      antialias: gl.getContextAttributes()?.antialias,
      samples: gl.getParameter(gl.SAMPLES),
    };
    // Inspect source geometry before view normalization, without retaining a second mesh copy.
    root.updateMatrixWorld(true);
    const before = root.parent;
    root.parent = null;
    root.updateMatrixWorld(true);
    const expanded = input.capture ? expandedSignatures(items).join("") : null;
    const signatures = expanded === null ? null : await sha256(new TextEncoder().encode(expanded));
    root.parent = before;
    const entry = performance.getEntriesByName(input.url, "resource").at(-1);
    const resource = entry instanceof PerformanceResourceTiming ? entry : undefined;
    return {
      outcome: "success",
      environment,
      source_bounds_mm: [sourceBounds.min.toArray(), sourceBounds.max.toArray()],
      source_bounds_manifest_absolute_errors_mm: [
        sourceBounds.min.toArray(),
        sourceBounds.max.toArray(),
      ].map((row, index) =>
        row.map((value, axis) => Math.abs(value - input.bounds_mm[index][axis])),
      ),
      triangle_count: triangleCount,
      timings: {
        download_ms: downloaded - start,
        parse_ms: parsed - downloaded,
        prepare_ms: prepared - parsed,
        first_draw_submit_ms: submitted - prepared,
        first_draw_gpu_complete_ms: completed - prepared,
        total_first_draw_gpu_complete_ms: completed - start,
      },
      timing_scope:
        "GPU completion via gl.finish; not compositor presentation; readback/parity excluded",
      transfer: {
        decoded_bytes: bytes.byteLength,
        encoded_body_bytes: resource?.encodedBodySize ?? null,
        transferred_bytes_including_http: resource?.transferSize ?? null,
      },
      cpu: memory,
      gpu: {
        buffer_storage_bytes: buffers.bytes,
        buffer_count: buffers.count,
        draw_calls: renderer.info.render.calls,
        submitted_triangles: renderer.info.render.triangles,
        drawing_buffer_width: gl.drawingBufferWidth,
        drawing_buffer_height: gl.drawingBufferHeight,
        framebuffer_estimate_bytes: width * height * 8 * Math.max(1, Number(environment.samples)),
        framebuffer_estimate_scope:
          "RGBA8 + assumed four-byte depth/stencil times reported MSAA samples; swapchain/driver overhead unknown",
        total_device_bytes: null,
        device_scope: "buffer storage calls exact; total physical/driver memory unavailable",
      },
      pixel_sha256: await sha256(pixel),
      foreground_pixels: pixel.filter((_value, index) => index % 4 === 3 && pixel[index] > 0)
        .length,
      capture: input.capture
        ? {
            signatures,
            position_normal_hex: expanded,
            rgba_base64: btoa(Array.from(pixel, (value) => String.fromCharCode(value)).join("")),
          }
        : null,
    };
  } finally {
    for (const mesh of items) mesh.geometry.dispose();
    for (const old of oldMaterials) old.dispose();
    material.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
  }
}

Object.assign(window, { viewerRepresentationPilot: { run, observeBuffers } });
