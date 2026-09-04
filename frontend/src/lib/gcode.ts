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

export function parseGcode(text: string): ToolpathData {
  let cx = 0,
    cy = 0,
    cz = 0,
    ce = 0;
  let relXYZ = false,
    relE = false;

  const extrudeSegs: number[] = [];
  const travelSegs: number[] = [];

  const layerRanges: LayerRange[] = [];
  let currentZ = -1;
  let layerVertStart = 0; // in vertex units (floats/3)

  let minX = Infinity,
    maxX = -Infinity;
  let minY = Infinity,
    maxY = -Infinity;
  let minZ = Infinity,
    maxZ = -Infinity;

  const lines = text.split("\n");

  for (const rawLine of lines) {
    let line = rawLine;
    const semi = line.indexOf(";");
    if (semi >= 0) line = line.slice(0, semi);
    line = line.trim();
    if (!line) continue;

    const tokens = line.split(/\s+/);
    const op = tokens[0].toUpperCase();

    if (op === "G90") {
      relXYZ = false;
      continue;
    }
    if (op === "G91") {
      relXYZ = true;
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
    if (op !== "G0" && op !== "G1" && op !== "G00" && op !== "G01") continue;

    let nx = cx,
      ny = cy,
      nz = cz,
      ne = ce;
    let hasE = false;

    for (let i = 1; i < tokens.length; i++) {
      const t = tokens[i].toUpperCase();
      if (t.length < 2) continue;
      const k = t[0];
      const v = parseFloat(t.slice(1));
      if (isNaN(v)) continue;
      if (k === "X") nx = relXYZ ? cx + v : v;
      else if (k === "Y") ny = relXYZ ? cy + v : v;
      else if (k === "Z") nz = relXYZ ? cz + v : v;
      else if (k === "E") {
        ne = relE ? ce + v : v;
        hasE = true;
      }
    }

    // Layer change: Z increases
    if (nz > cz && nz > 0.01) {
      if (currentZ >= 0) {
        const vCount = extrudeSegs.length / 3 - layerVertStart;
        layerRanges.push({ z: currentZ, vertexStart: layerVertStart, vertexCount: vCount });
      }
      currentZ = nz;
      layerVertStart = extrudeSegs.length / 3;
    } else if (currentZ < 0 && nz >= 0) {
      currentZ = nz;
    }

    const isExtrusion = hasE && (relE ? ne > 0 : ne > ce + 0.0001);
    if (hasE) ce = ne;

    // Track bounds only from extrusion moves so start-gcode travel to X0 Y0 doesn't skew center
    if (isExtrusion) {
      if (nx < minX) minX = nx;
      if (nx > maxX) maxX = nx;
      if (ny < minY) minY = ny;
      if (ny > maxY) maxY = ny;
      if (nz < minZ) minZ = nz;
      if (nz > maxZ) maxZ = nz;
    }

    const dx = nx - cx,
      dy = ny - cy,
      dz = nz - cz;
    if (dx !== 0 || dy !== 0 || dz !== 0) {
      // Map: three.x = gcodeX, three.y = gcodeZ (height), three.z = -gcodeY
      if (isExtrusion) {
        extrudeSegs.push(cx, cz, -cy, nx, nz, -ny);
      } else {
        travelSegs.push(cx, cz, -cy, nx, nz, -ny);
      }
    }

    cx = nx;
    cy = ny;
    cz = nz;
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
