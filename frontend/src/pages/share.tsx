"use client";

import { Suspense, lazy, useEffect, useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { AlertTriangle, Box, Download, Loader2 } from "lucide-react";

import { getAssetUrl } from "@/lib/api";
import { getSharedModel, sharedDownloadUrl, sharedStlUrl } from "@/lib/api/share";
import { formatBytes } from "@/lib/format";
import { PublicModelRead } from "@/types";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);

const MESH_TYPES = new Set(["stl", "3mf", "obj", "step"]);

export default function SharePage() {
  const { token = "" } = useParams();
  const [model, setModel] = useState<PublicModelRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSharedModel(token)
      .then((m) => {
        if (!cancelled) setModel(m);
      })
      .catch(() => {
        if (!cancelled) setError("This share link is invalid, expired, or revoked.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const meshFile = useMemo(
    () => model?.files.find((f) => MESH_TYPES.has(f.file_type)) ?? null,
    [model],
  );

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[var(--surface)]">
        <Loader2 className="h-6 w-6 animate-spin text-[var(--on-surface-variant)]" />
      </div>
    );
  }

  if (error || !model) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 bg-[var(--surface)] px-6 text-center">
        <AlertTriangle className="h-8 w-8 text-amber-500" />
        <p className="font-mono text-sm text-[var(--on-surface-variant)]">
          {error ?? "Not found."}
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-[var(--surface)] text-[var(--on-surface)]">
      <header className="border-b border-[var(--outline-variant)] px-6 py-4">
        <p className="font-mono text-[10px] uppercase tracking-widest text-[var(--on-surface-variant)]">
          Shared model · PrintStash
        </p>
        <h1 className="text-lg font-semibold mt-0.5">{model.name}</h1>
        {model.description && (
          <p className="text-sm text-[var(--on-surface-variant)] mt-1 max-w-2xl">
            {model.description}
          </p>
        )}
      </header>

      <main className="p-6 grid gap-6 lg:grid-cols-[1fr_320px]">
        <div className="rounded-md border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] overflow-hidden min-h-[60vh]">
          {meshFile ? (
            <Suspense
              fallback={
                <div className="h-full min-h-[60vh] flex items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-[var(--on-surface-variant)]" />
                </div>
              }
            >
              <STLViewer
                url={getAssetUrl(sharedStlUrl(token, meshFile.id))}
                screenshotName={model.name}
              />
            </Suspense>
          ) : (
            <div className="h-full min-h-[60vh] flex flex-col items-center justify-center gap-2 text-[var(--on-surface-variant)]">
              <Box className="h-8 w-8" />
              <p className="font-mono text-xs">
                No previewable 3D mesh in this share.
              </p>
            </div>
          )}
        </div>

        <aside className="space-y-3">
          <h2 className="font-mono text-[10px] uppercase tracking-widest text-[var(--on-surface-variant)]">
            Files ({model.files.length})
          </h2>
          {model.files.map((f) => (
            <div
              key={f.id}
              className="rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs truncate">{f.original_filename}</span>
                <span className="font-mono text-[10px] uppercase text-[var(--on-surface-variant)] shrink-0">
                  {f.file_type}
                </span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-2">
                <span className="font-mono text-[10px] text-[var(--on-surface-variant)]">
                  {formatBytes(f.size_bytes)}
                  {f.triangle_count
                    ? ` · ${f.triangle_count.toLocaleString()} tris`
                    : ""}
                </span>
                {model.allow_download && (
                  <a
                    href={getAssetUrl(sharedDownloadUrl(token, f.id))}
                    className="inline-flex items-center gap-1 font-mono text-[10px] uppercase tracking-wider text-[var(--primary)] hover:underline"
                  >
                    <Download className="h-3 w-3" /> Download
                  </a>
                )}
              </div>
            </div>
          ))}
          {!model.allow_download && (
            <p className="font-mono text-[10px] text-[var(--on-surface-variant)]/70">
              Downloads are disabled for this link — view only.
            </p>
          )}
        </aside>
      </main>
    </div>
  );
}
