"use client";

import { useState } from "react";
import { ChevronDown, ExternalLink } from "lucide-react";

import { getJson } from "@/lib/api/request";
import { toast } from "@/lib/toast";
import { DropdownMenu } from "@/components/ui/dropdown-menu";

type Slicer = {
  name: string;
  scheme: string;
  // File extensions this slicer can actually open from a URL.
  types: ReadonlySet<string>;
};

// Which file types each slicer opens from a URL. Bambu Studio only loads 3MF
// via URL (other formats error with "unknown format"); OrcaSlicer is broad.
// PrusaSlicer doesn't reliably open arbitrary self-hosted URLs yet
// (prusa3d/PrusaSlicer#13752) but is kept listed as best-effort.
const ORCA_TYPES = new Set(["stl", "3mf", "obj", "step", "gcode"]);
const SLICERS: Slicer[] = [
  { name: "OrcaSlicer", scheme: "orcaslicer", types: ORCA_TYPES },
  { name: "Bambu Studio", scheme: "bambustudio", types: new Set(["3mf"]) },
  { name: "PrusaSlicer", scheme: "prusaslicer", types: ORCA_TYPES },
];

function isMacOS() {
  if (typeof navigator === "undefined") return false;
  // navigator.platform is deprecated but still the most reliable signal here;
  // fall back to the user-agent string.
  const platform = navigator.platform ?? "";
  return /Mac/i.test(platform) || /Mac OS X/i.test(navigator.userAgent ?? "");
}

function slicerHref(scheme: string, fileUrl: string) {
  // Bambu Studio uses a different URL scheme on macOS: the file URL is
  // appended directly to the `bambustudioopen://` host instead of being passed
  // as an `open?file=` query parameter (issue #27).
  if (scheme === "bambustudio" && isMacOS()) {
    return `bambustudioopen://${encodeURIComponent(fileUrl)}`;
  }
  return `${scheme}://open?file=${encodeURIComponent(fileUrl)}`;
}

export function SlicerOpenButton({
  fileId,
  fileType,
  size = "md",
}: {
  fileId: number;
  fileType: string;
  size?: "sm" | "md";
}) {
  const [open, setOpen] = useState(false);

  const iconSize = size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4";
  const chevronSize = size === "sm" ? "h-2.5 w-2.5" : "h-3 w-3";

  const slicers = SLICERS.filter((s) => s.types.has(fileType));
  if (slicers.length === 0) return null;

  async function openInSlicer(scheme: string) {
    setOpen(false);
    try {
      // The slicer is a separate process with no login session, so it can't
      // send our bearer token. The backend returns a short-lived, filename-
      // bearing download URL (the path carries the extension so the slicer can
      // detect the format) with a file-scoped token embedded.
      const { url } = await getJson<{ url: string }>(
        `/api/v1/files/${fileId}/slicer-url`,
        { fresh: true },
      );
      const fileUrl = `${window.location.origin}${url}`;
      window.location.assign(slicerHref(scheme, fileUrl));
    } catch {
      toast.error("Couldn't open in slicer");
    }
  }

  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      align="end"
      role="menu"
      trigger={
        <button
          data-menu-trigger
          onClick={() => setOpen((o) => !o)}
          title="Open in slicer"
          aria-haspopup="menu"
          aria-expanded={open}
          className="inline-flex items-center gap-0.5 text-on-surface-variant hover:text-primary p-2 rounded hover:bg-surface-container-high transition-colors"
        >
          <ExternalLink className={iconSize} />
          <ChevronDown className={chevronSize} />
        </button>
      }
      contentClassName="min-w-[10rem] rounded border border-outline-variant bg-surface shadow-lg"
    >
      <p className="px-3 py-1.5 font-mono text-3xs uppercase tracking-wider text-on-surface-variant border-b border-outline-variant">
        Open in slicer
      </p>
      {slicers.map(({ name, scheme }) => (
        <button
          key={scheme}
          type="button"
          role="menuitem"
          onClick={() => openInSlicer(scheme)}
          className="block w-full px-3 py-2 text-left font-mono text-xs text-on-surface hover:bg-surface-container-low focus-visible:bg-surface-container-low outline-none transition-colors last:rounded-b"
        >
          {name}
        </button>
      ))}
    </DropdownMenu>
  );
}
