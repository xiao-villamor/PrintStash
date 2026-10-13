"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { formatBytes } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { meshLoadPolicy } from "@/lib/mesh-load-policy";

interface MeshPreviewGateProps {
  /** Stored size of the source Artifact the preview is made from. */
  sizeBytes: number;
  /** The Model's thumbnail, shown while the mesh has not been asked for. */
  posterUrl: string | null;
  posterAlt: string;
  /** The 3D viewer; mounted, and therefore downloading, only once admitted. */
  children: React.ReactNode;
}

/**
 * Admits a 3D viewer by the mesh load policy. A mesh over the budget keeps its
 * thumbnail on screen and offers the download as an explicit action; give the
 * gate a `key` per Artifact so a request never carries over to another mesh.
 */
export function MeshPreviewGate({
  sizeBytes,
  posterUrl,
  posterAlt,
  children,
}: MeshPreviewGateProps) {
  useUiLocale();
  const [requested, setRequested] = useState(false);
  if (requested || meshLoadPolicy(sizeBytes) === "automatic") return <>{children}</>;
  return (
    <div className="relative flex h-full w-full items-center justify-center">
      {posterUrl && (
        <img src={posterUrl} alt={posterAlt} className="max-h-full max-w-full object-contain" />
      )}
      <div className="absolute inset-x-0 bottom-4 flex flex-col items-center gap-2">
        <p className="rounded bg-surface-container px-2 py-1 text-xs text-on-surface-variant">
          {uiText("Large 3D preview · {size}", { size: formatBytes(sizeBytes) })}
        </p>
        <Button onClick={() => setRequested(true)}>{uiText("Load 3D preview")}</Button>
      </div>
    </div>
  );
}
