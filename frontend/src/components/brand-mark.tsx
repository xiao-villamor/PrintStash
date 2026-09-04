export function BrandMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox="268 118 426 426" fill="none" aria-hidden="true">
      {/* PrintStash mark: a bold "P" drawn as a 3D-printer extruder — carriage
          block + nozzle laying down a curl of molten filament. */}
      <g stroke="currentColor" strokeLinecap="round" strokeLinejoin="round">
        <path d="M397 350 V165 H485 A80 80 0 0 1 485 325 H397" strokeWidth="56" />
        <path
          d="M397 450 C397 470 381 480 363 480 C345 480 335 490 335 502 C335 514 347 520 361 520 H492"
          strokeWidth="30"
        />
      </g>
      <rect x="335" y="330" width="124" height="48" rx="14" fill="currentColor" />
      <rect x="375" y="382" width="44" height="22" rx="6" fill="currentColor" />
      <path d="M367 404 H427 L409 432 L397 442 L385 432 Z" fill="currentColor" />
    </svg>
  );
}
