export const brandMarkViewBox = { x: 268, y: 118, size: 426 };
export const brandMarkStrokes = [
  { d: "M397 350 V165 H485 A80 80 0 0 1 485 325 H397", width: 56 },
  {
    d: "M397 450 C397 470 381 480 363 480 C345 480 335 490 335 502 C335 514 347 520 361 520 H492",
    width: 30,
  },
];
export const brandMarkBlocks = [
  { x: 335, y: 330, width: 124, height: 48, rx: 14 },
  { x: 375, y: 382, width: 44, height: 22, rx: 6 },
];
export const brandMarkNozzle = "M367 404 H427 L409 432 L397 442 L385 432 Z";

export type Point = readonly [x: number, y: number];

const rasterSize = 192;
const printSize = 250;

let cachedFootprint: Point[][] | null | undefined;

export function* buildPrintFootprint(): Generator<void, Point[][] | null> {
  if (cachedFootprint === undefined) cachedFootprint = yield* rasterizeMark();
  return cachedFootprint;
}

function* rasterizeMark(): Generator<void, Point[][] | null> {
  if (!globalThis.Path2D) return null;

  const canvas = document.createElement("canvas");
  canvas.width = rasterSize;
  canvas.height = rasterSize;
  const context = canvas.getContext("2d");
  if (!context?.roundRect) return null;

  const { x, y, size } = brandMarkViewBox;
  const scale = rasterSize / size;
  context.setTransform(scale, 0, 0, scale, -x * scale, -y * scale);
  context.lineCap = "round";
  context.lineJoin = "round";
  for (const stroke of brandMarkStrokes) {
    context.lineWidth = stroke.width;
    context.stroke(new Path2D(stroke.d));
  }
  for (const block of brandMarkBlocks) {
    context.beginPath();
    context.roundRect(block.x, block.y, block.width, block.height, block.rx);
    context.fill();
  }
  context.fill(new Path2D(brandMarkNozzle));

  const { data } = context.getImageData(0, 0, rasterSize, rasterSize);
  yield;
  const contours = (yield* traceContours(data)).map(simplify);
  if (contours.length === 0) return null;

  const points = contours.flat();
  const xs = points.map(([px]) => px);
  const ys = points.map(([, py]) => py);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const minY = Math.min(...ys);
  const maxY = Math.max(...ys);
  const unitsPerPixel = printSize / Math.max(maxX - minX, maxY - minY);

  // Align the mark's right and down axes with the isometric view's screen axes.
  function onPlate([px, py]: Point): Point {
    const right = (px - (minX + maxX) / 2) * unitsPerPixel;
    const down = (py - (minY + maxY) / 2) * unitsPerPixel;
    return [(right + down) * Math.SQRT1_2, (down - right) * Math.SQRT1_2];
  }

  yield;
  return contours.map((contour) => contour.map(onPlate));
}

type Edge = { from: Point; to: Point; direction: number };

function* traceContours(data: Uint8ClampedArray): Generator<void, Point[][]> {
  const outgoing = new Map<number, Set<Edge>>();
  const edges = new Set<Edge>();
  const key = ([x, y]: Point) => y * (rasterSize + 1) + x;

  function filled(x: number, y: number): boolean {
    return (
      x >= 0 &&
      x < rasterSize &&
      y >= 0 &&
      y < rasterSize &&
      data[(y * rasterSize + x) * 4 + 3] >= 128
    );
  }

  function add(from: Point, to: Point, direction: number) {
    const edge = { from, to, direction };
    const start = key(from);
    let exits = outgoing.get(start);
    if (!exits) {
      exits = new Set();
      outgoing.set(start, exits);
    }
    exits.add(edge);
    edges.add(edge);
  }

  // Follow the pixel boundary with solid material on the right, including at holes.
  for (let y = 0; y < rasterSize; y += 1) {
    for (let x = 0; x < rasterSize; x += 1) {
      if (!filled(x, y)) continue;
      const topLeft: Point = [x, y];
      const topRight: Point = [x + 1, y];
      const bottomRight: Point = [x + 1, y + 1];
      const bottomLeft: Point = [x, y + 1];
      if (!filled(x, y - 1)) add(topLeft, topRight, 0);
      if (!filled(x + 1, y)) add(topRight, bottomRight, 1);
      if (!filled(x, y + 1)) add(bottomRight, bottomLeft, 2);
      if (!filled(x - 1, y)) add(bottomLeft, topLeft, 3);
    }
    if ((y + 1) % 48 === 0) yield;
  }

  const contours: Point[][] = [];
  for (const first of edges) {
    const contour = [first.from];
    let edge = first;
    while (true) {
      edges.delete(edge);
      outgoing.get(key(edge.from))?.delete(edge);
      contour.push(edge.to);
      if (key(edge.to) === key(first.from)) break;
      const exits = [...(outgoing.get(key(edge.to)) ?? [])];
      // At diagonally touching pixels, turn right to stay on the same boundary.
      const next = exits.find((exit) => exit.direction === (edge.direction + 1) % 4) ?? exits[0];
      if (!next) return [];
      edge = next;
    }
    // Ignore isolated raster pixels.
    if (contour.length > 5) contours.push(contour);
  }
  return contours;
}

function simplify(points: Point[]): Point[] {
  const first = points[0];
  const last = points[points.length - 1];
  const dx = last[0] - first[0];
  const dy = last[1] - first[1];
  const lengthSquared = dx * dx + dy * dy;
  let split = 0;
  let maxDistance = 0.8;
  for (let index = 1; index < points.length - 1; index += 1) {
    const [x, y] = points[index];
    const fraction = lengthSquared
      ? Math.max(0, Math.min(1, ((x - first[0]) * dx + (y - first[1]) * dy) / lengthSquared))
      : 0;
    const distance = Math.hypot(x - first[0] - dx * fraction, y - first[1] - dy * fraction);
    if (distance > maxDistance) {
      maxDistance = distance;
      split = index;
    }
  }
  if (split === 0) return [first, last];
  return [...simplify(points.slice(0, split)).slice(0, -1), ...simplify(points.slice(split))];
}

export function interpolate([x, y]: Point, [endX, endY]: Point, progress: number): Point {
  return [x + (endX - x) * progress, y + (endY - y) * progress];
}

function arcLengths(points: readonly Point[]): number[] {
  let length = 0;
  return points.map(([x, y], index) => {
    if (index > 0) {
      const [previousX, previousY] = points[index - 1];
      length += Math.hypot(x - previousX, y - previousY);
    }
    return length;
  });
}

function pointAt(points: readonly Point[], lengths: readonly number[], distance: number): Point {
  let low = 1;
  let high = lengths.length - 1;
  while (low < high) {
    const middle = (low + high) >>> 1;
    if (lengths[middle] < distance) low = middle + 1;
    else high = middle;
  }
  const start = lengths[low - 1];
  return interpolate(points[low - 1], points[low], (distance - start) / (lengths[low] - start));
}

export function smoothContour(closed: readonly Point[]) {
  // Round corners locally so long straight edges retain their shape.
  let points = closed.slice(0, -1);
  for (let pass = 0; pass < 3; pass += 1) {
    points = points.flatMap((point, index) => {
      const next = points[(index + 1) % points.length];
      const fraction = Math.min(0.25, 1.5 / Math.hypot(next[0] - point[0], next[1] - point[1]));
      return [interpolate(point, next, fraction), interpolate(next, point, fraction)];
    });
  }
  points.push(points[0]);

  return resamplePath(points);
}

export function resamplePath(points: readonly Point[]) {
  const lengths = arcLengths(points);
  const length = lengths[lengths.length - 1];
  const intervals = Math.ceil(length / 1.5);
  const samples = Array.from({ length: intervals + 1 }, (_, index) =>
    pointAt(points, lengths, (length * index) / intervals),
  );
  return { points: samples, lengths: arcLengths(samples) };
}

const infillSpacing = 11;
const infillInset = 3;

function scanCrossings(points: readonly Point[], y: number): number[] {
  const crossings: number[] = [];
  for (let index = 1; index < points.length; index += 1) {
    const [x1, y1] = points[index - 1];
    const [x2, y2] = points[index];
    if (y1 > y !== y2 > y) crossings.push(x1 + ((x2 - x1) * (y - y1)) / (y2 - y1));
  }
  return crossings;
}

export function buildInfill(contours: readonly (readonly Point[])[]): [Point, Point][] {
  const vertices = contours.flat();
  const rows = vertices.map(([, y]) => y);
  const minY = Math.min(...rows);
  const maxY = Math.max(...rows);

  // An edge's inset zone is a rectangle with a disk at each endpoint.
  const edgeStrips = contours.flatMap((contour) =>
    contour.slice(1).flatMap(([x2, y2], index) => {
      const [x1, y1] = contour[index];
      const length = Math.hypot(x2 - x1, y2 - y1);
      if (length === 0) return [];
      const dx = ((y1 - y2) * infillInset) / length;
      const dy = ((x2 - x1) * infillInset) / length;
      const corner: Point = [x1 + dx, y1 + dy];
      return [
        [
          corner,
          [x2 + dx, y2 + dy],
          [x2 - dx, y2 - dy],
          [x1 - dx, y1 - dy],
          corner,
        ] satisfies Point[],
      ];
    }),
  );

  const spans: [Point, Point][] = [];
  let reverse = false;
  const firstY =
    (minY + maxY) / 2 - Math.floor((maxY - minY) / (2 * infillSpacing)) * infillSpacing;
  for (let y = firstY; y < maxY; y += infillSpacing) {
    const crossings = contours
      .flatMap((contour) => scanCrossings(contour, y))
      .sort((a, b) => a - b);
    const blocked: [number, number][] = [];
    for (const strip of edgeStrips) {
      const xs = scanCrossings(strip, y);
      if (xs.length === 2) blocked.push([Math.min(...xs), Math.max(...xs)]);
    }
    for (const [x, vertexY] of vertices) {
      const distance = Math.abs(y - vertexY);
      if (distance >= infillInset) continue;
      const halfWidth = Math.sqrt(infillInset ** 2 - distance ** 2);
      blocked.push([x - halfWidth, x + halfWidth]);
    }
    blocked.sort(([a], [b]) => a - b);

    // Pair crossings by even-odd parity, then subtract all edge inset zones.
    const row: [Point, Point][] = [];
    for (let index = 0; index + 1 < crossings.length; index += 2) {
      let start = crossings[index];
      const end = crossings[index + 1];
      for (const [left, right] of blocked) {
        if (left >= end) break;
        if (right <= start) continue;
        if (left > start)
          row.push([
            [start, y],
            [left, y],
          ]);
        start = right;
      }
      if (start < end)
        row.push([
          [start, y],
          [end, y],
        ]);
    }
    if (row.length === 0) continue;
    spans.push(...(reverse ? row.reverse().map(([from, to]): [Point, Point] => [to, from]) : row));
    reverse = !reverse;
  }
  return spans;
}
