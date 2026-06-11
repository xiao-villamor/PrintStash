"use client";

import { useEffect, useRef, useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";

const SLICERS = [
  { name: "OrcaSlicer", scheme: "orcaslicer" },
  { name: "Bambu Studio", scheme: "bambustudio" },
  { name: "PrusaSlicer", scheme: "prusaslicer" },
];

export function SlicerOpenButton({
  fileId,
  size = "md",
}: {
  fileId: number;
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(false);
  const [origin, setOrigin] = useState("");
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setOrigin(window.location.origin);
  }, []);

  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const iconSize = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";
  const chevronSize = size === "sm" ? "h-2.5 w-2.5" : "h-3 w-3";

  function slicerHref(scheme: string) {
    const fileUrl = `${origin}/api/v1/files/${fileId}/download`;
    return `${scheme}://open?file=${encodeURIComponent(fileUrl)}`;
  }

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen((o) => !o)}
        title="Open in slicer"
        className="inline-flex items-center gap-0.5 text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors"
      >
        <ExternalLink className={iconSize} />
        <ChevronDown className={chevronSize} />
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 min-w-[10rem] rounded border border-[var(--outline-variant)] bg-[var(--surface)] shadow-lg">
          <p className="px-3 py-1.5 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] border-b border-[var(--outline-variant)]">
            Open in slicer
          </p>
          {SLICERS.map(({ name, scheme }) => (
            <a
              key={scheme}
              href={slicerHref(scheme)}
              onClick={() => setOpen(false)}
              className="block w-full px-3 py-2 text-left font-mono text-xs text-[var(--on-surface)] hover:bg-[var(--surface-container-low)] transition-colors last:rounded-b"
            >
              {name}
            </a>
          ))}
        </div>
      )}
    </div>
  );
}
