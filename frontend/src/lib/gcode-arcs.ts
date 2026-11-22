/** Circular interpolation in the selected G-code plane, with a bounded chord error. */
export type Point = [number, number, number];
export type Plane = "xy" | "xz" | "yz";
export interface ArcOptions {
  clockwise: boolean;
  plane: Plane;
  center: Point;
  radius?: number;
  segmentLimit: number;
}

// A preview chord stays within 20 microns of the commanded circle. A fixed
// 0.25 mm chord massively over-tessellates the broad arcs slicers emit.
const MAX_CHORD_ERROR_MM = 0.02;
const MAX_CHORD_ANGLE = Math.PI / 36;

function sweep(start: number, end: number, clockwise: boolean): number {
  let angle = end - start;
  if (clockwise) {
    while (angle >= -1e-10) angle -= 2 * Math.PI;
  } else {
    while (angle <= 1e-10) angle += 2 * Math.PI;
  }
  return angle;
}

export function* arcPoints(start: Point, end: Point, options: ArcOptions): Generator<Point> {
  const [u, v, w] =
    options.plane === "xy"
      ? ([0, 1, 2] as const)
      : options.plane === "xz"
        ? ([2, 0, 1] as const)
        : ([1, 2, 0] as const);
  let cu = options.center[u],
    cv = options.center[v];
  if (options.radius !== undefined) {
    const radius = Math.abs(options.radius);
    const du = end[u] - start[u],
      dv = end[v] - start[v];
    const chord = Math.hypot(du, dv);
    if (chord < 1e-9 || chord > 2 * radius + 1e-6) throw new Error("toolpath_invalid_arc");
    const height = Math.sqrt(Math.max(0, radius * radius - (chord * chord) / 4));
    const midU = (start[u] + end[u]) / 2,
      midV = (start[v] + end[v]) / 2;
    for (const sign of [1, -1]) {
      cu = midU - (sign * dv * height) / chord;
      cv = midV + (sign * du * height) / chord;
      const angle = Math.abs(
        sweep(
          Math.atan2(start[v] - cv, start[u] - cu),
          Math.atan2(end[v] - cv, end[u] - cu),
          options.clockwise,
        ),
      );
      if (options.radius < 0 ? angle >= Math.PI - 1e-8 : angle <= Math.PI + 1e-8) break;
    }
  }
  const radius = Math.hypot(start[u] - cu, start[v] - cv);
  if (
    !Number.isFinite(radius) ||
    radius < 1e-9 ||
    Math.abs(Math.hypot(end[u] - cu, end[v] - cv) - radius) > Math.max(0.05, radius * 0.001)
  ) {
    throw new Error("toolpath_invalid_arc");
  }
  const initial = Math.atan2(start[v] - cv, start[u] - cu);
  const angle = sweep(initial, Math.atan2(end[v] - cv, end[u] - cu), options.clockwise);
  // This form of the sagitta formula remains stable for very large radii.
  const errorAngle = 4 * Math.asin(Math.sqrt(Math.min(1, MAX_CHORD_ERROR_MM / (2 * radius))));
  const count = Math.max(1, Math.ceil(Math.abs(angle) / Math.min(MAX_CHORD_ANGLE, errorAngle)));
  if (count > options.segmentLimit) throw new Error("toolpath_segment_limit");
  for (let index = 1; index <= count; index++) {
    if (index === count) {
      yield end;
      continue;
    }
    const point: Point = [0, 0, 0];
    const fraction = index / count;
    point[u] = cu + radius * Math.cos(initial + angle * fraction);
    point[v] = cv + radius * Math.sin(initial + angle * fraction);
    point[w] = start[w] + (end[w] - start[w]) * fraction;
    yield point;
  }
}
