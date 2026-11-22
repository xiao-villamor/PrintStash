import {
  brandMarkBlocks,
  brandMarkNozzle,
  brandMarkStrokes,
  brandMarkViewBox,
} from "@/components/auth-print-geometry";

export function BrandMark({ className = "h-5 w-5" }: { className?: string }) {
  const { x, y, size } = brandMarkViewBox;
  return (
    <svg className={className} viewBox={`${x} ${y} ${size} ${size}`} fill="none" aria-hidden="true">
      {/* PrintStash mark: a bold "P" drawn as a 3D-printer extruder — carriage
          block + nozzle laying down a curl of molten filament. */}
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
        {brandMarkStrokes.map(({ d, width }) => (
          <path key={d} d={d} strokeWidth={width} />
        ))}
      </g>
      {brandMarkBlocks.map((block) => (
        <rect key={block.y} {...block} fill="currentColor" />
      ))}
      <path d={brandMarkNozzle} fill="currentColor" />
    </svg>
  );
}
