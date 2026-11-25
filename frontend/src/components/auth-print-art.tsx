import { useEffect, useRef } from "react";
import {
  buildInfill,
  buildPrintFootprint,
  interpolate,
  resamplePath,
  smoothContour,
  type Point,
} from "@/components/auth-print-geometry";
import { DURATION } from "@/lib/overlay";

const plateHalfSize = 240;
const gridStep = 20;
const layerCount = 4;
const layerHeight = 10.5;
const topHeight = layerCount * layerHeight;
const approachMs = 500;
const layerMs = 3_200;
const parkMs = 1_000;
const infillMs = 4_500;
const outlinesEnd = approachMs + layerCount * layerMs;
const infillStart = outlinesEnd + DURATION.press;
const parkStart = infillStart + infillMs;
const printDurationMs = parkStart + parkMs;
const maxFrameDeltaMs = 34;
const parkedPoint = project([185, 85], topHeight + 12);

// Keep the print's progress across sign-in remounts within the same page load.
let elapsedMs = 0;

function project([x, y]: Point, height = 0): Point {
  return [(x - y) * 0.75, (x + y) * 0.39 - height];
}

function outline(points: readonly Point[]): string {
  return points.map((point) => project(point).join(",")).join(" ");
}

const plateOutline = outline([
  [-plateHalfSize, -plateHalfSize],
  [plateHalfSize, -plateHalfSize],
  [plateHalfSize, plateHalfSize],
  [-plateHalfSize, plateHalfSize],
]);

const plateGrid = Array.from({ length: (2 * plateHalfSize) / gridStep - 1 }, (_, index) => {
  const offset = (index + 1) * gridStep - plateHalfSize;
  return (
    `M${project([offset, -plateHalfSize])}L${project([offset, plateHalfSize])}` +
    `M${project([-plateHalfSize, offset])}L${project([plateHalfSize, offset])}`
  );
}).join(" ");

function progress(elapsed: number, start: number, duration: number): number {
  return Math.max(0, Math.min(1, (elapsed - start) / duration));
}

function ease(progress: number): number {
  return progress * progress * (3 - 2 * progress);
}

function svgElement<Tag extends keyof SVGElementTagNameMap>(tag: Tag): SVGElementTagNameMap[Tag] {
  return document.createElementNS("http://www.w3.org/2000/svg", tag);
}

type Curve = ReturnType<typeof smoothContour>;
type PrintStep =
  | { kind: "travel"; from: Point; to: Point; weight: number }
  | {
      kind: "contour";
      points: Point[];
      lengths: number[];
      path: SVGPathElement;
      fullPath: string;
      weight: number;
    };

function preparePrint(
  curves: Curve[],
  infill: Curve[],
  root: SVGGElement,
  travelLine: SVGPathElement,
  nozzle: SVGRectElement,
) {
  let previousEnd: Point | undefined;
  function buildPass(paths: Curve[], group: SVGGElement, height: number) {
    const steps: PrintStep[] = [];
    for (const curve of paths) {
      const points = curve.points.map(([x, y]): Point => [x, y - height]);
      const start = points[0];
      if (previousEnd) {
        // Smoothstep peaks at 1.5 times its average speed.
        const weight = 1.5 * Math.hypot(start[0] - previousEnd[0], start[1] - previousEnd[1]);
        if (weight > 0) steps.push({ kind: "travel", from: previousEnd, to: start, weight });
      }
      const path = svgElement("path");
      path.setAttribute("vector-effect", "non-scaling-stroke");
      group.append(path);
      steps.push({
        kind: "contour",
        points,
        lengths: curve.lengths,
        path,
        fullPath: `M${points.join("L")}`,
        weight: curve.lengths[curve.lengths.length - 1],
      });
      previousEnd = points[points.length - 1];
    }

    let weight = 0;
    const timeline = steps.map((step) => {
      weight += step.weight;
      return { step, end: weight };
    });
    return { timeline, weight };
  }

  const footprint = curves.map(({ points }) => `M${points.join("L")}Z`).join("");
  const layers = Array.from({ length: layerCount }, (_, index) => {
    const height = (index + 1) * layerHeight;
    const group = svgElement("g");
    group.setAttribute("stroke-width", "1.35");
    root.append(group);

    const fill = svgElement("path");
    fill.setAttribute("d", footprint);
    fill.setAttribute("transform", `translate(0,${-height})`);
    fill.setAttribute("fill", "var(--card)");
    fill.setAttribute("fill-rule", "evenodd");
    fill.setAttribute("stroke", "none");
    fill.setAttribute("opacity", "0");
    group.append(fill);

    return { group, fill, ...buildPass(curves, group, height) };
  });
  const [x, y] = curves[0].points[0];
  const first: Point = [x, y - layerHeight];
  const lastOutline = previousEnd;
  const infillGroup = svgElement("g");
  infillGroup.setAttribute("stroke-width", "1");
  root.append(infillGroup);
  const top = buildPass(infill, infillGroup, topHeight);
  const last = previousEnd;
  if (!lastOutline || !last) throw new Error("Print geometry requires a footprint");

  let layerIndex = 0;
  let stepIndex = 0;
  let sampleIndex = 0;
  let prefix = "";
  let traveling = false;
  let fading: { fill: SVGPathElement; start: number } | undefined;
  let infillFinished = false;

  function showTip(tip: Point, travelFrom?: Point) {
    if (travelFrom) {
      travelLine.setAttribute("d", `M${travelFrom}L${tip}`);
    } else if (traveling) {
      travelLine.removeAttribute("d");
    }
    traveling = travelFrom !== undefined;
    nozzle.setAttribute("transform", `translate(${tip})`);
  }

  function finishStep(step: PrintStep) {
    if (step.kind === "contour") step.path.setAttribute("d", step.fullPath);
    stepIndex += 1;
    sampleIndex = 0;
    prefix = "";
  }

  return (elapsed: number) => {
    if (elapsed < approachMs) {
      showTip(interpolate([first[0], first[1] - 18.5], first, ease(elapsed / approachMs)));
      return;
    }

    const printElapsed = elapsed - approachMs;
    const targetLayer = Math.min(layerCount, Math.floor(printElapsed / layerMs));
    while (layerIndex < targetLayer) {
      const { group, fill, timeline } = layers[layerIndex];
      while (stepIndex < timeline.length) finishStep(timeline[stepIndex].step);
      if (layerIndex < layerCount - 1) group.setAttribute("stroke-opacity", "0.62");
      const start = approachMs + (layerIndex + 1) * layerMs;
      if (elapsed >= start + DURATION.press) {
        fill.setAttribute("opacity", "1");
      } else {
        fading = { fill, start };
      }
      layerIndex += 1;
      stepIndex = 0;
    }

    if (fading) {
      const opacity = progress(elapsed, fading.start, DURATION.press);
      fading.fill.setAttribute("opacity", String(opacity));
      if (opacity === 1) fading = undefined;
    }

    if (elapsed >= parkStart) {
      if (!infillFinished) {
        while (stepIndex < top.timeline.length) finishStep(top.timeline[stepIndex].step);
        infillFinished = true;
      }
      showTip(interpolate(last, parkedPoint, ease(progress(elapsed, parkStart, parkMs))));
      return;
    }
    if (elapsed >= outlinesEnd && elapsed < infillStart) {
      showTip(lastOutline);
      return;
    }

    const printingTop = elapsed >= infillStart;
    const { timeline, weight } = printingTop ? top : layers[layerIndex];
    if (timeline.length === 0) {
      showTip(lastOutline);
      return;
    }
    const distance = printingTop
      ? progress(elapsed, infillStart, infillMs) * weight
      : ((printElapsed - layerIndex * layerMs) / layerMs) * weight;
    while (stepIndex < timeline.length - 1 && distance >= timeline[stepIndex].end) {
      finishStep(timeline[stepIndex].step);
    }
    const { step } = timeline[stepIndex];
    const start = stepIndex === 0 ? 0 : timeline[stepIndex - 1].end;
    const stepDistance = distance - start;
    if (step.kind === "travel") {
      showTip(interpolate(step.from, step.to, ease(stepDistance / step.weight)), step.from);
      return;
    }

    const { path, points, lengths } = step;
    if (prefix === "") prefix = `M${points[0]}`;
    while (sampleIndex < points.length - 2 && stepDistance >= lengths[sampleIndex + 1]) {
      sampleIndex += 1;
      prefix += `L${points[sampleIndex]}`;
    }
    const segmentStart = lengths[sampleIndex];
    const segmentLength = lengths[sampleIndex + 1] - segmentStart;
    const tip = interpolate(
      points[sampleIndex],
      points[sampleIndex + 1],
      progress(stepDistance, segmentStart, segmentLength),
    );
    path.setAttribute("d", `${prefix}L${tip}`);
    showTip(tip);
  };
}

export function AuthPrintArt({ animated = true }: { animated?: boolean }) {
  const layersRef = useRef<SVGGElement>(null);
  const travelRef = useRef<SVGPathElement>(null);
  const nozzleRef = useRef<SVGRectElement>(null);

  useEffect(() => {
    const layers = layersRef.current;
    const travelLine = travelRef.current;
    const nozzle = nozzleRef.current;
    if (!layers || !travelLine || !nozzle) return;

    // Match the breakpoint that displays .auth-split-art.
    const desktop = window.matchMedia("(min-width: 1024px)");
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const footprint = buildPrintFootprint();
    let draw: ((elapsed: number) => void) | undefined;
    let frame: number | undefined;
    let previousTime: number | undefined;
    let geometryUnavailable = false;
    let platePainted = false;

    function stop() {
      if (frame !== undefined) window.cancelAnimationFrame(frame);
      frame = undefined;
      previousTime = undefined;
    }

    function tick(time: number) {
      frame = undefined;
      if (!desktop.matches || !draw) {
        stop();
        return;
      }
      if (reducedMotion.matches) {
        elapsedMs = printDurationMs;
      } else if (previousTime !== undefined) {
        elapsedMs = Math.min(
          printDurationMs,
          elapsedMs + Math.min(time - previousTime, maxFrameDeltaMs),
        );
      }
      previousTime = time;
      draw(elapsedMs);
      if (elapsedMs < printDurationMs) frame = window.requestAnimationFrame(tick);
    }

    const build = () => {
      frame = undefined;
      if (!desktop.matches) return;
      // Let the sign-in form paint before the first canvas readback.
      if (!platePainted) {
        platePainted = true;
        frame = window.requestAnimationFrame(build);
        return;
      }
      const result = footprint.next();
      if (!result.done) {
        frame = window.requestAnimationFrame(build);
        return;
      }
      if (!result.value) {
        geometryUnavailable = true;
        return;
      }
      const curves = result.value.map((contour) =>
        smoothContour(contour.map((point) => project(point))),
      );
      // Clip the plate-space scanlines against the same smoothed contours used for the fills.
      const plateContours = curves.map(({ points }) =>
        points.map(([x, y]): Point => [x / 1.5 + y / 0.78, y / 0.78 - x / 1.5]),
      );
      const infill = buildInfill(plateContours).map((span) =>
        resamplePath(span.map((point) => project(point))),
      );
      draw = preparePrint(curves, infill, layers, travelLine, nozzle);
      nozzle.removeAttribute("visibility");
      sync();
    };

    function sync() {
      if (!desktop.matches) {
        stop();
        return;
      }
      if (reducedMotion.matches) elapsedMs = printDurationMs;
      if (!draw) {
        if (!geometryUnavailable && frame === undefined)
          frame = window.requestAnimationFrame(build);
        return;
      }
      if (!animated || elapsedMs >= printDurationMs) {
        stop();
        draw(printDurationMs);
      } else if (frame === undefined) {
        draw(elapsedMs);
        frame = window.requestAnimationFrame(tick);
      }
    }

    sync();
    desktop.addEventListener("change", sync);
    reducedMotion.addEventListener("change", sync);
    return () => {
      stop();
      desktop.removeEventListener("change", sync);
      reducedMotion.removeEventListener("change", sync);
      footprint.return(null);
      layers.replaceChildren();
      travelLine.removeAttribute("d");
      nozzle.setAttribute("visibility", "hidden");
    };
  }, [animated]);

  return (
    <svg
      className="pointer-events-none h-full w-full"
      viewBox="-390 -225 780 430"
      preserveAspectRatio="xMidYMid meet"
      fill="none"
      strokeWidth="1"
      aria-hidden="true"
      focusable="false"
    >
      <g stroke="var(--auth-art-grid)">
        <polygon
          points={plateOutline}
          fill="var(--auth-art-bed)"
          vectorEffect="non-scaling-stroke"
        />
        <path d={plateGrid} vectorEffect="non-scaling-stroke" />
      </g>
      <g ref={layersRef} stroke="var(--auth-art-stroke)" />
      <g stroke="var(--auth-art-stroke)">
        <path
          ref={travelRef}
          strokeWidth={0.5}
          strokeDasharray="3 3"
          vectorEffect="non-scaling-stroke"
        />
        <rect
          ref={nozzleRef}
          x="-3"
          y="-3"
          width="6"
          height="6"
          visibility="hidden"
          vectorEffect="non-scaling-stroke"
        />
      </g>
    </svg>
  );
}
