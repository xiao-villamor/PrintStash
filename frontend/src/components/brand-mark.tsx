import { brandMarkFold, brandMarkOutline, brandMarkViewBox } from "@/components/brand-geometry";

export function BrandMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={className} viewBox={`0 0 ${brandMarkViewBox} ${brandMarkViewBox}`} aria-hidden>
      <path d={brandMarkOutline} className="fill-brand" />
      <path d={brandMarkFold} className="fill-brand-fold" />
    </svg>
  );
}
