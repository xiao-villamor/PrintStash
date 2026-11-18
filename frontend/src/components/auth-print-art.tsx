type Point = readonly [x: number, y: number];

const plateHalfSize = 240;
const gridStep = 20;
const trayRadius = 116;
const layerCount = 12;
const layerHeight = 3.5;
const topHeight = layerCount * layerHeight;
const hexagonSlope = Math.sqrt(3) / 2;

function project([x, y]: Point, height = 0): string {
  return `${(x - y) * 0.75},${(x + y) * 0.39 - height}`;
}

function outline(points: readonly Point[], height = 0): string {
  return points.map((point) => project(point, height)).join(" ");
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

const trayCorners: Point[] = [
  [-trayRadius, 0],
  [-trayRadius / 2, -trayRadius * hexagonSlope],
  [trayRadius / 2, -trayRadius * hexagonSlope],
  [trayRadius, 0],
  [trayRadius / 2, trayRadius * hexagonSlope],
  [-trayRadius / 2, trayRadius * hexagonSlope],
];

const layers = Array.from({ length: layerCount - 1 }, (_, index) => {
  const height = (index + 1) * layerHeight;
  return { height, points: outline(trayCorners, height) };
});

const infillHalfRows = 7;
const infillSpacing = 12;
const infillLastY = infillHalfRows * infillSpacing;

function infillEdge(y: number): number {
  return trayRadius - 8 - Math.abs(y) / (2 * hexagonSlope);
}

// Alternate the direction of each pass along the inside of the hexagonal rim.
const infill = Array.from({ length: 2 * infillHalfRows + 1 }, (_, index) => {
  const y = index * infillSpacing - infillLastY;
  const edge = infillEdge(y);
  const start = index % 2 === 0 ? -edge : edge;
  return outline(
    [
      [start, y],
      [-start, y],
    ],
    topHeight,
  );
}).join(" ");

const travelStart: Point = [infillEdge(infillLastY), infillLastY];
const travelEnd: Point = [110, 130];

export function AuthPrintArt() {
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
      <g stroke="var(--muted-foreground)" strokeOpacity={0.24}>
        <path d={plateGrid} vectorEffect="non-scaling-stroke" />
        <polygon points={plateOutline} vectorEffect="non-scaling-stroke" />
      </g>
      <g stroke="var(--muted-foreground)" strokeOpacity={0.5}>
        <polygon
          points={outline(trayCorners)}
          fill="var(--card)"
          vectorEffect="non-scaling-stroke"
        />
        {layers.map(({ height, points }) => (
          <polygon key={height} points={points} vectorEffect="non-scaling-stroke" />
        ))}
      </g>
      <g stroke="var(--foreground)">
        <polygon points={outline(trayCorners, topHeight)} vectorEffect="non-scaling-stroke" />
        <polyline points={infill} vectorEffect="non-scaling-stroke" />
        <path
          d={`M${project(travelStart, topHeight)}L${project(travelEnd, topHeight)}`}
          strokeDasharray="3 4"
          strokeOpacity={0.55}
          vectorEffect="non-scaling-stroke"
        />
        <rect
          x="-3"
          y="-3"
          width="6"
          height="6"
          transform={`translate(${project(travelEnd, topHeight)})`}
          vectorEffect="non-scaling-stroke"
        />
      </g>
    </svg>
  );
}
