import { arcPoints, type Plane, type Point } from "./gcode-arcs";

// G-code toolpath parsing. Lives outside the viewer component so the module
// stays component-only and Fast Refresh keeps working for the viewer.

// ---- Types ----

interface LayerRange {
  z: number;
  vertexStart: number; // index into extrudePositions (in floats / 3)
  vertexCount: number;
}

export interface ToolpathData {
  extrudePositions: Float32Array;
  extrudeColors: Float32Array;
  travelPositions: Float32Array;
  layerRanges: LayerRange[];
  cumulativeVertices: Uint32Array; // cumulative vertex count per layer (length = layerRanges.length + 1)
  totalLayers: number;
  bounds: {
    sizeX: number;
    sizeY: number;
    sizeZ: number;
    maxDim: number;
  };
}

// ---- G-code Parser ----

function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const a = s * Math.min(l, 1 - l);
  const f = (n: number) => {
    const k = (n + h / 30) % 12;
    return l - a * Math.max(-1, Math.min(k - 3, 9 - k, 1));
  };
  return [f(0), f(8), f(4)];
}

export function parseGcode(text: string, segmentLimit = 1_000_000): ToolpathData {
  let position: Point = [0, 0, 0];
  const offset: Point = [0, 0, 0];
  let ce = 0,
    scale = 1;
  let relXYZ = false,
    relE = false,
    absoluteCenter = false;
  let plane: Plane = "xy";
  let segments = 0;
  const extrudeSegs: number[] = [];
  const travelSegs: number[] = [];
  const layerRanges: LayerRange[] = [];
  let currentZ = -1,
    layerVertStart = 0;
  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;
  let minZ = Infinity,
    maxZ = -Infinity;

  const append = (next: Point, extruding: boolean) => {
    const [x, y, z] = position;
    const [nx, ny, nz] = next;
    if (x !== nx || y !== ny || z !== nz) {
      if (++segments > segmentLimit) throw new Error("toolpath_segment_limit");
      (extruding ? extrudeSegs : travelSegs).push(x, z, -y, nx, nz, -ny);
      if (extruding) {
        minX = Math.min(minX, x, nx);
        maxX = Math.max(maxX, x, nx);
        minY = Math.min(minY, y, ny);
        maxY = Math.max(maxY, y, ny);
        minZ = Math.min(minZ, z, nz);
        maxZ = Math.max(maxZ, z, nz);
      }
    }
    position = next;
  };

  for (const rawLine of text.split("\n")) {
    const line = rawLine
      .split(/[;*]/, 1)[0]
      .replace(/\([^)]*\)/g, "")
      .toUpperCase();
    const tokens = [...line.matchAll(/([A-Z])([+-]?(?:\d+\.?\d*|\.\d+))/g)];
    const command = tokens.find((token) => token[1] === "G" || token[1] === "M");
    if (!command) continue;
    const op = command[1] + Number(command[2]);
    const values: Record<string, number> = {};
    for (const token of tokens) {
      if (!["G", "M", "N"].includes(token[1])) {
        const value = Number(token[2]);
        if (!Number.isFinite(value)) throw new Error("toolpath_invalid_coordinate");
        values[token[1]] = value;
      }
    }
    if (op === "G90") {
      relXYZ = false;
      continue;
    }
    if (op === "G91") {
      relXYZ = true;
      continue;
    }
    if (op === "G90.1") {
      absoluteCenter = true;
      continue;
    }
    if (op === "G91.1") {
      absoluteCenter = false;
      continue;
    }
    if (op === "M82") {
      relE = false;
      continue;
    }
    if (op === "M83") {
      relE = true;
      continue;
    }
    if (op === "G20") {
      scale = 25.4;
      continue;
    }
    if (op === "G21") {
      scale = 1;
      continue;
    }
    if (op === "G17") {
      plane = "xy";
      continue;
    }
    if (op === "G18") {
      plane = "xz";
      continue;
    }
    if (op === "G19") {
      plane = "yz";
      continue;
    }
    if (op === "G92") {
      const all = !["X", "Y", "Z", "E"].some((axis) => values[axis] !== undefined);
      (["X", "Y", "Z"] as const).forEach((axis, index) => {
        if (all || values[axis] !== undefined)
          offset[index] = position[index] - (values[axis] ?? 0) * scale;
      });
      if (all || values.E !== undefined) ce = (values.E ?? 0) * scale;
      continue;
    }
    if (!["G0", "G1", "G2", "G3"].includes(op)) continue;
    const next: Point = [position[0], position[1], position[2]];
    (["X", "Y", "Z"] as const).forEach((axis, index) => {
      if (values[axis] !== undefined)
        next[index] = values[axis] * scale + (relXYZ ? position[index] : offset[index]);
    });
    const ne = values.E === undefined ? ce : values.E * scale + (relE ? ce : 0);
    const extruding = values.E !== undefined && ne > ce + 0.0001;
    ce = ne;
    const nz = next[2];
    if (nz > position[2] && nz > 0.01) {
      if (currentZ >= 0)
        layerRanges.push({
          z: currentZ,
          vertexStart: layerVertStart,
          vertexCount: extrudeSegs.length / 3 - layerVertStart,
        });
      currentZ = nz;
      layerVertStart = extrudeSegs.length / 3;
    } else if (currentZ < 0 && nz >= 0) currentZ = nz;
    if (op === "G2" || op === "G3") {
      const center: Point = [position[0], position[1], position[2]];
      (["I", "J", "K"] as const).forEach((axis, index) => {
        center[index] = absoluteCenter
          ? (values[axis] ?? (position[index] - offset[index]) / scale) * scale + offset[index]
          : position[index] + (values[axis] ?? 0) * scale;
      });
      for (const point of arcPoints(position, next, {
        clockwise: op === "G2",
        plane,
        center,
        radius: values.R === undefined ? undefined : values.R * scale,
        segmentLimit: segmentLimit - segments,
      }))
        append(point, extruding);
    } else append(next, extruding);
  }

  // Push final layer
  if (currentZ >= 0) {
    const vCount = extrudeSegs.length / 3 - layerVertStart;
    layerRanges.push({ z: currentZ, vertexStart: layerVertStart, vertexCount: vCount });
  }

  if (minX === Infinity) {
    minX = 0;
    maxX = 200;
    minY = 0;
    maxY = 200;
    minZ = 0;
    maxZ = 20;
  }

  // Center coordinates
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2; // gcode Y
  const centerZ = (minZ + maxZ) / 2; // gcode Z (height)

  const extArr = new Float32Array(extrudeSegs);
  const travArr = new Float32Array(travelSegs);

  for (let i = 0; i < extArr.length; i += 3) {
    extArr[i] -= centerX;
    extArr[i + 1] -= centerZ;
    extArr[i + 2] += centerY; // three.z was -gcodeY, center is -(centerY), shift = +centerY
  }
  for (let i = 0; i < travArr.length; i += 3) {
    travArr[i] -= centerX;
    travArr[i + 1] -= centerZ;
    travArr[i + 2] += centerY;
  }

  // Per-vertex colors based on Y (height) in three.js space
  const totalVerts = extArr.length / 3;
  const colArr = new Float32Array(totalVerts * 3);
  const heightRange = maxZ - minZ || 1;

  for (let vi = 0; vi < totalVerts; vi++) {
    const threeY = extArr[vi * 3 + 1]; // centered, three.y = gcodeZ - centerZ
    const gcodeZ = threeY + centerZ;
    const t = Math.max(0, Math.min(1, (gcodeZ - minZ) / heightRange));
    // Blue (240°) at bottom → red (0°) at top
    const hue = (1 - t) * 240;
    const [r, g, b] = hslToRgb(hue, 0.9, 0.55);
    colArr[vi * 3] = r;
    colArr[vi * 3 + 1] = g;
    colArr[vi * 3 + 2] = b;
  }

  // Cumulative vertex counts for layer slider
  const cumulative = new Uint32Array(layerRanges.length + 1);
  cumulative[0] = 0;
  for (let i = 0; i < layerRanges.length; i++) {
    cumulative[i + 1] = cumulative[i] + layerRanges[i].vertexCount;
  }

  const sizeX = maxX - minX;
  const sizeY = maxY - minY;
  const sizeZ = maxZ - minZ;

  return {
    extrudePositions: extArr,
    extrudeColors: colArr,
    travelPositions: travArr,
    layerRanges,
    cumulativeVertices: cumulative,
    totalLayers: layerRanges.length,
    bounds: { sizeX, sizeY, sizeZ, maxDim: Math.max(sizeX, sizeY, sizeZ) || 1 },
  };
}
