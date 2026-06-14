export function BrandMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
    >
      {/* PrintStash mark: one extruded "filament" strand — molten bead + loose
          coil at the bottom, curling up into a P */}
      <path
        d="M9.3 24.1 L9.3 22.2 A2.2 2.2 0 1 1 11.5 20 L11.5 7 H15.5 A4.6 4.6 0 0 1 15.5 16.2 H11.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <circle cx="9.3" cy="24.1" r="1.7" fill="currentColor" />
    </svg>
  );
}
