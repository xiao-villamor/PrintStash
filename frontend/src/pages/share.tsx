"use client";

import { Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { AlertTriangle, Box, Download, Layers, Loader2 } from "lucide-react";

import type { STLViewerControls, ViewerDisplayMode } from "@/components/stl-viewer";
import { getAssetUrl } from "@/lib/api";
import {
  getSharedModel,
  sharedDownloadUrl,
  sharedGcodeUrl,
  sharedStlUrl,
} from "@/lib/api/share";
import { formatBytes, formatDuration } from "@/lib/format";
import { PublicFileRead, PublicModelRead } from "@/types";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);

const GcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then((m) => ({ default: m.GcodeViewer })),
);

const MESH_TYPES = new Set(["stl", "3mf", "obj", "step"]);
type ShareViewerMode = "model" | "gcode";

function value(value: string | number | null | undefined, suffix = "") {
  if (value === null || value === undefined || value === "") return "—";
  return `${value}${suffix}`;
}

function revisionTitle(file: PublicFileRead) {
  return `Rev ${file.gcode_revision_number ?? file.version}${file.revision_label ? ` · ${file.revision_label}` : ""}`;
}

export default function SharePage() {
  const { token = "" } = useParams();
  const [model, setModel] = useState<PublicModelRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [viewerMode, setViewerMode] = useState<ShareViewerMode>("model");
  const [displayMode, setDisplayMode] = useState<ViewerDisplayMode>("solid");
  const [showGrid, setShowGrid] = useState(true);
  const viewerControls = useRef<STLViewerControls | null>(null);

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

  useEffect(() => {
    document.title = model
      ? `${model.name} · Shared · PrintStash`
      : "Shared model · PrintStash";
  }, [model]);

  const meshFile = useMemo(
    () => model?.files.find((f) => MESH_TYPES.has(f.file_type)) ?? null,
    [model],
  );
  const gcodeFiles = useMemo(
    () => model?.files.filter((f) => f.file_type === "gcode") ?? [],
    [model],
  );
  const selectedGcode = useMemo(
    () => gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1] ?? null,
    [gcodeFiles],
  );
  const canShowModel = !!meshFile;
  const canShowGcode = !!selectedGcode;
  const activeViewerMode: ShareViewerMode =
    viewerMode === "gcode" && canShowGcode ? "gcode" : canShowModel ? "model" : "gcode";

  useEffect(() => {
    if (!model) return;
    setViewerMode(meshFile ? "model" : "gcode");
  }, [model, meshFile]);

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-surface">
        <Loader2 className="h-6 w-6 animate-spin text-on-surface-variant" />
      </div>
    );
  }

  if (error || !model) {
    return (
      <div className="min-h-screen flex flex-col items-center justify-center gap-3 bg-surface px-6 text-center">
        <AlertTriangle className="h-8 w-8 text-amber-500" />
        <p className="font-mono text-sm text-on-surface-variant">
          {error ?? "Not found."}
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-surface text-on-surface">
      <header className="border-b border-outline-variant bg-surface-container-lowest px-6 py-4">
        <p className="font-mono text-3xs uppercase tracking-widest text-on-surface-variant">
          Shared model · PrintStash
        </p>
        <div className="mt-0.5 flex flex-wrap items-end justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold leading-tight truncate">{model.name}</h1>
            <p className="mt-1 font-mono text-2xs text-on-surface-variant">
              {meshFile ? `${meshFile.file_type.toUpperCase()} source · ` : ""}
              {gcodeFiles.length} G-code revision{gcodeFiles.length === 1 ? "" : "s"}
              {selectedGcode ? ` · ${revisionTitle(selectedGcode)}` : ""}
            </p>
          </div>
          <div className="flex rounded border border-outline-variant bg-surface-container-low overflow-hidden">
            <button
              type="button"
              onClick={() => setViewerMode("model")}
              disabled={!canShowModel}
              className={`h-9 px-3 inline-flex items-center gap-1.5 font-mono text-2xs uppercase tracking-wider transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                activeViewerMode === "model"
                  ? "bg-accent text-accent-foreground"
                  : "text-on-surface-variant hover:bg-surface-container-high"
              }`}
              title={canShowModel ? "3D model view" : "No mesh in this share"}
            >
              <Box className="h-3.5 w-3.5" /> 3D
            </button>
            <button
              type="button"
              onClick={() => setViewerMode("gcode")}
              disabled={!canShowGcode}
              className={`h-9 px-3 inline-flex items-center gap-1.5 font-mono text-2xs uppercase tracking-wider transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                activeViewerMode === "gcode"
                  ? "bg-accent text-accent-foreground"
                  : "text-on-surface-variant hover:bg-surface-container-high"
              }`}
              title={canShowGcode ? "G-code toolpath preview" : "No G-code in this share"}
            >
              <Layers className="h-3.5 w-3.5" /> G-code
            </button>
          </div>
        </div>
        {model.description && (
          <p className="text-sm text-on-surface-variant mt-1 max-w-2xl">
            {model.description}
          </p>
        )}
      </header>

      <main className="p-4 md:p-6 grid gap-4 md:gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="relative rounded-md border border-outline-variant bg-surface-container-low overflow-hidden min-h-[62vh]">
          {activeViewerMode === "gcode" && selectedGcode ? (
            <Suspense
              fallback={
                <div className="h-full min-h-[62vh] flex items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-on-surface-variant" />
                </div>
              }
            >
              <GcodeViewer
                url={sharedGcodeUrl(token, selectedGcode.id)}
                screenshotName={model.name}
              />
            </Suspense>
          ) : activeViewerMode === "model" && meshFile ? (
            <Suspense
              fallback={
                <div className="h-full min-h-[62vh] flex items-center justify-center">
                  <Loader2 className="h-6 w-6 animate-spin text-on-surface-variant" />
                </div>
              }
            >
              <STLViewer
                url={getAssetUrl(sharedStlUrl(token, meshFile.id))}
                onControlsReady={(api) => { viewerControls.current = api; }}
                displayMode={displayMode}
                showGrid={showGrid}
                screenshotName={model.name}
              />
            </Suspense>
          ) : (
            <div className="h-full min-h-[60vh] flex flex-col items-center justify-center gap-2 text-on-surface-variant">
              <Box className="h-8 w-8" />
              <p className="font-mono text-xs">
                No previewable mesh or G-code in this share.
              </p>
            </div>
          )}

          {activeViewerMode === "model" && meshFile && (
            <div className="absolute top-4 left-4 z-10 flex flex-wrap items-center gap-1.5">
              <div className="flex rounded border border-outline-variant bg-surface-container-lowest/90 backdrop-blur overflow-hidden shadow-sm">
                {(["solid", "xray", "wireframe"] as ViewerDisplayMode[]).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => setDisplayMode(mode)}
                    className={`h-9 px-2.5 font-mono text-2xs uppercase tracking-wider transition-colors ${
                      displayMode === mode
                        ? "bg-accent text-accent-foreground"
                        : "text-on-surface-variant hover:bg-surface-container-high"
                    }`}
                  >
                    {mode === "wireframe" ? "Wire" : mode === "xray" ? "X-Ray" : "Solid"}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={() => setShowGrid((current) => !current)}
                className={`h-9 px-2.5 rounded border border-outline-variant bg-surface-container-lowest/90 backdrop-blur font-mono text-2xs uppercase tracking-wider shadow-sm transition-colors ${
                  showGrid ? "text-primary" : "text-on-surface-variant"
                }`}
              >
                Grid
              </button>
              <button
                type="button"
                onClick={() => viewerControls.current?.fit()}
                className="h-9 px-2.5 rounded border border-outline-variant bg-surface-container-lowest/90 backdrop-blur font-mono text-2xs uppercase tracking-wider text-on-surface-variant shadow-sm hover:bg-surface-container-high"
              >
                Fit
              </button>
            </div>
          )}

          {(meshFile || selectedGcode) && (
            <div className="absolute top-4 right-4 z-10 max-w-[min(70%,360px)]">
              <div className="rounded border border-outline-variant bg-surface-container-lowest/90 backdrop-blur px-2.5 py-1.5 text-right shadow-sm">
                <p className="font-mono text-2xs text-on-surface truncate">
                  {activeViewerMode === "gcode"
                    ? selectedGcode?.original_filename
                    : meshFile?.original_filename}
                </p>
                <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
                  {activeViewerMode === "gcode" ? "G-code toolpath" : "Source model"}
                </p>
              </div>
            </div>
          )}
        </div>

        <aside className="space-y-3">
          {selectedGcode && (
            <div className="rounded border border-outline-variant bg-surface-container-lowest p-3">
              <div className="flex items-center justify-between gap-2">
                <h2 className="font-mono text-3xs uppercase tracking-widest text-on-surface-variant">
                  Shared revision
                </h2>
                <span className="font-mono text-3xs uppercase text-primary">
                  G-code preview
                </span>
              </div>
              <p className="mt-2 text-sm font-medium text-on-surface">
                {revisionTitle(selectedGcode)}
              </p>
              {selectedGcode.revision_notes && (
                <p className="mt-1 text-xs text-on-surface-variant">
                  {selectedGcode.revision_notes}
                </p>
              )}
              <div className="mt-3 grid grid-cols-2 gap-2 text-2xs">
                <div>
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Status</span>
                  <span>{selectedGcode.revision_status?.replace("_", " ") ?? "—"}</span>
                </div>
                <div>
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Print time</span>
                  <span>{formatDuration(selectedGcode.estimated_time_s)}</span>
                </div>
                <div>
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Layer</span>
                  <span>{value(selectedGcode.layer_height_mm, " mm")}</span>
                </div>
                <div>
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Nozzle</span>
                  <span>{value(selectedGcode.nozzle_diameter_mm, " mm")}</span>
                </div>
                <div>
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Material</span>
                  <span>{selectedGcode.material_type ?? "—"}</span>
                </div>
                <div>
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Filament</span>
                  <span>{value(selectedGcode.filament_weight_g, " g")}</span>
                </div>
                <div className="col-span-2">
                  <span className="block font-mono text-3xs uppercase text-on-surface-variant">Printer</span>
                  <span>{selectedGcode.printer_model ?? "—"}</span>
                </div>
              </div>
            </div>
          )}

          <h2 className="font-mono text-3xs uppercase tracking-widest text-on-surface-variant">
            Files ({model.files.length})
          </h2>
          {model.files.map((f) => (
            <div
              key={f.id}
              className="rounded border border-outline-variant bg-surface-container-lowest p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs truncate">{f.original_filename}</span>
                <span className="font-mono text-3xs uppercase text-on-surface-variant shrink-0">
                  {f.file_type}
                </span>
              </div>
              {f.file_type === "gcode" && (
                <div className="mt-1 flex items-center gap-1 font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
                  <Layers className="h-3 w-3" />
                  {revisionTitle(f)}
                  {f.is_recommended ? " · Recommended" : ""}
                </div>
              )}
              <div className="mt-1 flex items-center justify-between gap-2">
                <span className="font-mono text-3xs text-on-surface-variant">
                  {formatBytes(f.size_bytes)}
                  {f.triangle_count
                    ? ` · ${f.triangle_count.toLocaleString()} tris`
                    : ""}
                </span>
                {model.allow_download && (
                  <a
                    href={getAssetUrl(sharedDownloadUrl(token, f.id))}
                    className="inline-flex items-center gap-1 font-mono text-3xs uppercase tracking-wider text-primary hover:underline"
                  >
                    <Download className="h-3 w-3" /> Download
                  </a>
                )}
              </div>
            </div>
          ))}
          {!model.allow_download && (
            <p className="font-mono text-3xs text-on-surface-variant/70">
              Downloads are disabled for this link — view only.
            </p>
          )}
        </aside>
      </main>
    </div>
  );
}
