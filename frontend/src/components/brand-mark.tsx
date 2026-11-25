import { brandMarkPath, brandMarkSize } from "@/components/auth-print-geometry";

export function BrandMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox={`0 0 ${brandMarkSize} ${brandMarkSize}`}
      fill="currentColor"
      aria-hidden="true"
      focusable="false"
    >
      <path d={brandMarkPath} fillRule="evenodd" />
    </svg>
  );
}
