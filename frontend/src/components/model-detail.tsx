"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { STLViewerControls } from "@/components/stl-viewer";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  CategoryRead,
  FileRead,
  FileRevisionStatus,
  MetadataRead,
  ModelRead,
  ModelPrinterFileRead,
  PrinterRead,
  TagRead,
} from "@/types";

const STLViewer = dynamic(
  () => import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
  { ssr: false, loading: () => <Loader2 className="h-8 w-8 animate-spin text-[var(--on-surface-variant)]" /> },
);
import {
  createTag,
  deleteModel,
  getAssetUrl,
  getModelPrinterFiles,
  listCategories,
  listPrinters,
  listTags,
  sendToPrinter,
  updateFileRevision,
  updateModel,
  addGcodeRevision,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { createTask, updateTask } from "@/lib/task-center";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Check,
  ChevronDown,
  Download,
  FileText,
  GitCompare,
  Loader2,
  Minus,
  Pencil,
  Plus,
  RotateCcw,
  Send,
  Star,
  Trash2,
  Wifi,
  WifiOff,
  X,
} from "lucide-react";

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i];
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

function formatMillimeters(value: number | null | undefined): string {
  return value ? `${value}mm` : "—";
}

function formatPercent(value: number | null | undefined): string {
  return value ? `${value}%` : "—";
}

function formatGrams(value: number | null | undefined): string {
  return value ? `${value}g` : "—";
}

function formatTemperature(value: number | null | undefined): string {
  return value ? `${value}°C` : "—";
}

function formatCost(value: number | null | undefined): string {
  return value ? value.toFixed(2) : "—";
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(dateStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

const REVISION_STATUS_LABELS: Record<FileRevisionStatus, string> = {
  known_good: "Known good",
  needs_test: "Needs test",
  failed: "Failed",
  archived: "Archived",
};

function revisionStatusClass(status: FileRevisionStatus | null): string {
  switch (status) {
    case "known_good":
      return "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
    case "needs_test":
      return "bg-amber-500/15 text-amber-600 border-amber-500/30";
    case "failed":
      return "bg-[var(--error-container)]/40 text-[var(--error)] border-[var(--error)]/30";
    case "archived":
      return "bg-[var(--surface-container-high)] text-[var(--on-surface-variant)] border-[var(--outline-variant)]";
    default:
      return "bg-[var(--surface-container-low)] text-[var(--on-surface-variant)] border-[var(--outline-variant)]";
  }
}

function revisionStatusLabel(status: FileRevisionStatus | null): string {
  return status ? REVISION_STATUS_LABELS[status] : "Unmarked";
}

type PrintSettingRow = {
  label: string;
  value: string;
  chip?: boolean;
  highlight?: boolean;
};

function buildPrintSettingRows(meta: MetadataRead | null | undefined): PrintSettingRow[] {
  const rows: PrintSettingRow[] = [
    { label: "PRINTER PROFILE", value: meta?.printer_model ?? "—" },
    {
      label: "MATERIAL",
      value: meta?.material_type ?? "—",
      chip: true,
    },
  ];

  if (meta?.material_brand) {
    rows.push({ label: "FILAMENT PROFILE", value: meta.material_brand });
  }

  rows.push(
    { label: "LAYER HEIGHT", value: formatMillimeters(meta?.layer_height_mm) },
  );

  if (meta?.first_layer_height_mm) {
    rows.push({
      label: "FIRST LAYER",
      value: formatMillimeters(meta.first_layer_height_mm),
    });
  }

  rows.push(
    { label: "NOZZLE", value: formatMillimeters(meta?.nozzle_diameter_mm) },
    { label: "INFILL", value: formatPercent(meta?.infill_percent) },
  );

  if (meta?.wall_loops) {
    rows.push({ label: "WALLS", value: String(meta.wall_loops) });
  }

  if (meta?.top_shell_layers || meta?.bottom_shell_layers) {
    rows.push({
      label: "TOP / BOTTOM",
      value: `${meta?.top_shell_layers ?? "—"} / ${meta?.bottom_shell_layers ?? "—"}`,
    });
  }

  if (meta?.support_material !== null && meta?.support_material !== undefined) {
    rows.push({ label: "SUPPORTS", value: meta.support_material ? "Yes" : "No" });
  }

  if (meta?.nozzle_temperature_c) {
    rows.push({
      label: "NOZZLE TEMP",
      value: formatTemperature(meta.nozzle_temperature_c),
    });
  }

  if (meta?.bed_temperature_c) {
    rows.push({ label: "BED TEMP", value: formatTemperature(meta.bed_temperature_c) });
  }

  rows.push(
    {
      label: "EST. TIME",
      value: formatDuration(meta?.estimated_time_s ?? null),
      highlight: true,
    },
    { label: "FILAMENT", value: formatGrams(meta?.filament_weight_g) },
  );

  if (meta?.filament_cost) {
    rows.push({ label: "FILAMENT COST", value: formatCost(meta.filament_cost) });
  }

  return rows;
}

export function ModelDetail({ model: initialModel }: { model: ModelRead }) {
  const router = useRouter();
  const auth = useRequireAuth();
  const initialGcodeFiles = initialModel.files.filter((f) => f.file_type === "gcode");
  const [model, setModel] = useState(initialModel);
  const [deleting, setDeleting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [editName, setEditName] = useState(model.name);
  const [editDescription, setEditDescription] = useState(model.description || "");
  const [editCategory, setEditCategory] = useState(model.category || "");
  const [editTags, setEditTags] = useState<string[]>([...model.tags]);
  const [catOpen, setCatOpen] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [categories, setCategories] = useState<CategoryRead[]>([]);
  const [tags, setTags] = useState<TagRead[]>([]);
  const [catLoaded, setCatLoaded] = useState(false);
  const [revisionSaving, setRevisionSaving] = useState<number | null>(null);
  const [editingRevisionId, setEditingRevisionId] = useState<number | null>(null);
  const [addRevisionOpen, setAddRevisionOpen] = useState(false);
  const [revisionLabel, setRevisionLabel] = useState("");
  const [revisionStatus, setRevisionStatus] = useState<FileRevisionStatus | "">("");
  const [revisionNotes, setRevisionNotes] = useState("");
  const [revisionRecommended, setRevisionRecommended] = useState(false);
  const [compareLeftId, setCompareLeftId] = useState<number>(initialGcodeFiles.at(-1)?.id ?? 0);
  const [compareRightId, setCompareRightId] = useState<number>(initialGcodeFiles.at(-2)?.id ?? initialGcodeFiles.at(-1)?.id ?? 0);
  const [printerFiles, setPrinterFiles] = useState<ModelPrinterFileRead[]>([]);
  const viewerControls = useRef<STLViewerControls | null>(null);

  useEffect(() => {
    getModelPrinterFiles(model.id).then(setPrinterFiles).catch(() => {});
  }, [model.id]);

  async function doDelete() {
    if (!confirm("Delete this model? This cannot be undone.")) return;
    setDeleting(true);
    try {
      await deleteModel(model.id);
      toast.success("Model deleted");
      router.push("/");
      router.refresh();
    } catch (e) {
      toast.error(e);
    } finally {
      setDeleting(false);
    }
  }

  function enterEdit() {
    setEditName(model.name);
    setEditDescription(model.description || "");
    setEditCategory(model.category || "");
    setEditTags([...model.tags]);
    setTagInput("");
    setCatOpen(false);
    if (!catLoaded) {
      listCategories().then((c) => { setCategories(c); setCatLoaded(true); }).catch(() => {});
      listTags().then(setTags).catch(() => {});
    }
    setEditing(true);
  }

  function cancelEdit() {
    setEditing(false);
  }

  async function saveEdit() {
    setSaving(true);
    try {
      const updated = await updateModel(model.id, {
        name: editName.trim() || undefined,
        description: editDescription.trim() || undefined,
        category: editCategory || undefined,
        tags: editTags.length ? editTags : undefined,
      });
      setModel(updated);
      setEditing(false);
      toast.success("Model updated");
    } catch (e) {
      toast.error(e);
    } finally {
      setSaving(false);
    }
  }

  function editToggleTag(slug: string) {
    setEditTags((p) =>
      p.includes(slug) ? p.filter((s) => s !== slug) : [...p, slug],
    );
  }

  async function editCreateTag(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    const existing = tags.find(
      (t) => t.name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (existing) {
      if (!editTags.includes(existing.slug)) editToggleTag(existing.slug);
      return;
    }
    try {
      const t = await createTag({ name: trimmed });
      setTags((p) => [...p, t]);
      setEditTags((p) => [...p, t.slug]);
    } catch {
      /* ignored */
    }
  }

  const editFilteredTags = useMemo(() => {
    const q = tagInput.toLowerCase().trim();
    return tags.filter(
      (t) =>
        !editTags.includes(t.slug) &&
        (q === "" || t.name.toLowerCase().includes(q)),
    );
  }, [tags, tagInput, editTags]);

  const editCanCreate =
    tagInput.trim().length > 0 &&
    !tags.find(
      (t) => t.name.toLowerCase() === tagInput.trim().toLowerCase(),
    );

  const sortedFiles = useMemo(
    () => [...model.files].sort((a, b) => a.version - b.version),
    [model.files],
  );
  const gcodeFiles = useMemo(
    () => sortedFiles.filter((f) => f.file_type === "gcode"),
    [sortedFiles],
  );
  const sourceFiles = useMemo(
    () => sortedFiles.filter((f) => f.file_type !== "gcode"),
    [sortedFiles],
  );
  const recommendedGcode = gcodeFiles.find((f) => f.is_recommended) ?? null;
  const latestFile = recommendedGcode ?? gcodeFiles[gcodeFiles.length - 1] ?? sortedFiles[sortedFiles.length - 1];
  const meta = latestFile?.metadata;
  const printSettingRows = useMemo(() => buildPrintSettingRows(meta), [meta]);
  const meshFile = model.files.find(
    (f) => f.file_type === "stl" || f.file_type === "3mf" || f.file_type === "obj",
  );
  const hasGcode = gcodeFiles.length > 0;
  const compareLeft = gcodeFiles.find((f) => f.id === compareLeftId) ?? gcodeFiles[gcodeFiles.length - 1] ?? null;
  const compareRight = gcodeFiles.find((f) => f.id === compareRightId) ?? gcodeFiles[gcodeFiles.length - 2] ?? null;
  const thumbUrl = model.thumbnail_url ? getAssetUrl(model.thumbnail_url) : null;
  const printerFilesByFileId = useMemo(() => {
    const grouped = new Map<number, ModelPrinterFileRead[]>();
    for (const row of printerFiles) {
      if (row.missing_since) continue;
      grouped.set(row.file_id, [...(grouped.get(row.file_id) ?? []), row]);
    }
    return grouped;
  }, [printerFiles]);

  function startRevisionEdit(file: FileRead) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setEditingRevisionId(file.id);
    setRevisionLabel(file.revision_label ?? "");
    setRevisionStatus(file.revision_status ?? "");
    setRevisionNotes(file.revision_notes ?? "");
    setRevisionRecommended(file.is_recommended);
  }

  async function saveRevision(file: FileRead) {
    setRevisionSaving(file.id);
    try {
      const updated = await updateFileRevision(model.id, file.id, {
        revision_label: revisionLabel,
        revision_status: revisionStatus || null,
        revision_notes: revisionNotes,
        is_recommended: revisionRecommended,
      });
      setModel(updated);
      setEditingRevisionId(null);
      toast.success("Revision updated");
    } catch (e) {
      toast.error(e);
    } finally {
      setRevisionSaving(null);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {addRevisionOpen && (
        <AddGcodeRevisionModal
          modelId={model.id}
          onClose={() => setAddRevisionOpen(false)}
          onUploaded={(updated) => {
            setModel(updated);
            setAddRevisionOpen(false);
            toast.success("G-code revision added");
          }}
        />
      )}
      {/* Detail Header */}
      <header className="h-auto md:h-16 flex flex-wrap items-center justify-between px-4 md:px-6 py-3 md:py-0 gap-2 border-b border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] shrink-0">
        <div className="flex items-center gap-4">
          <Link
            href="/"
            className="w-10 h-10 flex items-center justify-center rounded hover:bg-[var(--surface-container-high)] text-[var(--on-surface-variant)] transition-colors"
          >
            <ArrowLeft className="h-5 w-5" />
          </Link>
          <div>
            {editing ? (
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full bg-[var(--surface)] text-[var(--on-surface)] font-mono text-lg border border-[var(--outline-variant)] rounded px-2 py-0.5 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                placeholder="Model name"
              />
            ) : (
              <h1 className="text-xl font-semibold text-[var(--on-surface)] leading-tight">
                {model.name}
              </h1>
            )}
            <span className="font-mono text-[13px] text-[var(--on-surface-variant)]">
              Last updated: {timeAgo(model.updated_at)}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {editing ? (
            <>
              <button
                onClick={cancelEdit}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider"
              >
                Cancel
              </button>
              <button
                onClick={saveEdit}
                disabled={saving}
                className="px-4 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50"
              >
                {saving ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Saving…</>
                ) : (
                  <><Check className="h-4 w-4" /> Save</>
                )}
              </button>
            </>
          ) : (
            <>
              <button
                onClick={auth.isAuthenticated ? enterEdit : auth.showAuthRequiredToast}
                disabled={!auth.isAuthenticated}
                title={auth.blockReason ?? "Edit model"}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Pencil className="h-4 w-4" /> {auth.isAuthenticated ? "Edit" : "Sign in to edit"}
              </button>
              <button
                onClick={auth.isAuthenticated ? doDelete : auth.showAuthRequiredToast}
                disabled={deleting || !auth.isAuthenticated}
                title={auth.blockReason ?? "Delete model"}
                className="px-4 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors font-mono text-xs uppercase tracking-wider flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {deleting ? (
                  <><Loader2 className="h-4 w-4 animate-spin" /> Deleting...</>
                ) : auth.isAuthenticated ? (
                  <><Trash2 className="h-4 w-4" /> Delete</>
                ) : (
                  <><Trash2 className="h-4 w-4" /> Sign in to delete</>
                )}
              </button>
            </>
          )}
        </div>
      </header>

      {/* Two-Column Layout */}
      <div className="flex-1 flex flex-col md:flex-row min-h-0 overflow-hidden md:overflow-hidden">
        {/* Left: 3D Model Preview */}
        <div className="flex-1 min-h-[250px] md:min-h-0 bg-[var(--surface-container-low)] relative border-b md:border-b-0 md:border-r border-[var(--outline-variant)] flex items-center justify-center m-2 md:m-4 rounded overflow-hidden"
          style={{ boxShadow: "inset 0 0 0 1px var(--surface-variant)" }}>
          {meshFile ? (
            <Suspense fallback={
              <div className="flex items-center justify-center text-[var(--on-surface-variant)]">
                <Loader2 className="h-8 w-8 animate-spin" />
              </div>
            }>
              <STLViewer
                url={getAssetUrl(`/api/v1/files/${meshFile.id}/stl`)}
                onControlsReady={(api) => { viewerControls.current = api; }}
              />
            </Suspense>
          ) : thumbUrl ? (
            <img
              src={thumbUrl}
              alt={model.name}
              className="max-w-full max-h-full object-contain"
            />
          ) : (
            <div className="flex items-center justify-center text-[var(--on-surface-variant)]">
              <FileText className="h-20 w-20 opacity-20" />
            </div>
          )}

          {/* 3D Viewer Controls */}
          <div className="absolute bottom-4 right-4 flex flex-col gap-1 z-10">
            <div className="flex bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded overflow-hidden shadow-sm">
              <button
                onClick={() => viewerControls.current?.zoomIn()}
                className="w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors border-r border-[var(--outline-variant)]"
                title="Zoom in"
              >
                <Plus className="h-4 w-4" />
              </button>
              <button
                onClick={() => viewerControls.current?.zoomOut()}
                className="w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors"
                title="Zoom out"
              >
                <Minus className="h-4 w-4" />
              </button>
            </div>
            <button
              onClick={() => viewerControls.current?.resetView()}
              className="h-9 px-3 bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded shadow-sm flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors"
              title="Reset view"
            >
              <RotateCcw className="h-4 w-4" />
            </button>
          </div>

          {/* Dimensions overlay */}
          {meta?.bbox_x_mm && meta?.bbox_y_mm && meta?.bbox_z_mm && (
            <div className="absolute top-4 left-4 z-10">
              <div className="bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded px-2 py-1 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-500" />
                <span className="font-mono text-[13px] text-[var(--on-surface)]">
                  {meta.bbox_x_mm}×{meta.bbox_y_mm}×{meta.bbox_z_mm} mm
                </span>
              </div>
            </div>
          )}
        </div>

        {/* Right: Settings & Files Panel */}
        <div className="md:w-[400px] bg-[var(--surface-container-lowest)] border-l-0 md:border-l border-t md:border-t-0 border-[var(--outline-variant)] flex flex-col h-auto md:h-full shrink-0 min-h-0">
          <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 md:space-y-8">
            {/* Print Settings */}
            <section>
              <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
                Print Settings
              </h2>
              <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded flex flex-col">
                {printSettingRows.map((row, index) => (
                  <SettingRow
                    key={row.label}
                    label={row.label}
                    value={row.value}
                    chip={row.chip}
                    highlight={row.highlight}
                    last={index === printSettingRows.length - 1}
                  />
                ))}
              </div>
            </section>

            {/* Mesh Geometry */}
            {(meta?.volume_mm3 || meta?.triangle_count) && (
              <section>
                <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
                  Mesh Geometry
                </h2>
                <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded flex flex-col">
                  {meta?.volume_mm3 && (
                    <SettingRow
                      label="VOLUME"
                      value={meta.volume_mm3 < 1000 ? `${meta.volume_mm3.toFixed(1)} mm³` : `${(meta.volume_mm3 / 1000).toFixed(2)} cm³`}
                    />
                  )}
                  {meta?.triangle_count && (
                    <SettingRow label="TRIANGLES" value={meta.triangle_count.toLocaleString()} last />
                  )}
                </div>
              </section>
            )}

            {/* Slicer info */}
            {meta?.slicer_name && (
              <p className="font-mono text-xs text-[var(--on-surface-variant)]">
                Sliced with {meta.slicer_name}
                {meta.slicer_version ? ` v${meta.slicer_version}` : ""}
              </p>
            )}

            {/* Tags & Category */}
            {editing ? (
              <div className="space-y-4">
                {/* Category picker */}
                <div>
                  <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
                    Category
                  </label>
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setCatOpen((v) => !v)}
                      className="w-full h-10 flex items-center justify-between bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                    >
                      <span className={editCategory ? "" : "text-[var(--on-surface-variant)]/60"}>
                        {editCategory || "None"}
                      </span>
                      <ChevronDown className="h-4 w-4 text-[var(--on-surface-variant)]" />
                    </button>
                    {catOpen && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setCatOpen(false)} />
                        <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-56 overflow-y-auto">
                          <button
                            type="button"
                            onClick={() => { setEditCategory(""); setCatOpen(false); }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                          >
                            None
                          </button>
                          {categories.map((c) => (
                            <button
                              key={c.id}
                              type="button"
                              onClick={() => { setEditCategory(c.path); setCatOpen(false); }}
                              className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                                editCategory === c.path
                                  ? "text-[var(--primary)] bg-[var(--secondary-container)]"
                                  : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                              }`}
                            >
                              {c.path} <span className="opacity-50">({c.model_count})</span>
                            </button>
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                </div>
                {/* Description */}
                <div>
                  <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
                    Description
                  </label>
                  <textarea
                    value={editDescription}
                    onChange={(e) => setEditDescription(e.target.value)}
                    rows={2}
                    className="w-full bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent resize-none"
                    placeholder="Optional description"
                  />
                </div>
                {/* Tags */}
                <div>
                  <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
                    Tags
                  </label>
                  <div className="relative">
                    <input
                      value={tagInput}
                      onChange={(e) => setTagInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && tagInput.trim()) {
                          e.preventDefault();
                          editCreateTag(tagInput);
                          setTagInput("");
                        } else if (e.key === "Backspace" && !tagInput && editTags.length) {
                          setEditTags((p) => p.slice(0, -1));
                        }
                      }}
                      placeholder="Search or create — press Enter"
                      className="w-full h-10 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                    />
                    {tagInput && (editFilteredTags.length > 0 || editCanCreate) && (
                      <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-40 overflow-y-auto">
                        {editFilteredTags.slice(0, 6).map((t) => (
                          <button
                            key={t.id}
                            type="button"
                            onClick={() => { editToggleTag(t.slug); setTagInput(""); }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] flex justify-between"
                          >
                            <span>{t.name}</span>
                            <span className="opacity-50">({t.model_count})</span>
                          </button>
                        ))}
                        {editCanCreate && (
                          <button
                            type="button"
                            onClick={() => { editCreateTag(tagInput); setTagInput(""); }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--primary)] hover:bg-[var(--surface-container-low)] flex items-center gap-2"
                          >
                            <Plus className="h-3 w-3" /> Create &quot;{tagInput.trim()}&quot;
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                  {editTags.length > 0 && (
                    <div className="flex flex-wrap gap-1.5 mt-2">
                      {editTags.map((slug) => {
                        const t = tags.find((x) => x.slug === slug);
                        return (
                          <span key={slug} className="inline-flex items-center gap-1 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] pl-2 pr-1 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider">
                            {t?.name || slug}
                            <button type="button" onClick={() => editToggleTag(slug)} aria-label={`Remove ${t?.name || slug}`} className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-[var(--on-secondary-container)]/10">
                              <X className="h-3 w-3" />
                            </button>
                          </span>
                        );
                      })}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {model.category && (
                  <span className="bg-[var(--surface-container)] text-[var(--on-surface)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
                    {model.category}
                  </span>
                )}
                {model.tags.map((t) => (
                  <span key={t} className="bg-[var(--secondary-container)] text-[var(--on-secondary-container)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
                    {t}
                  </span>
                ))}
              </div>
            )}

            {/* G-code Revisions */}
            <section>
              <div className="mb-4 flex items-center justify-between gap-3 border-b border-[var(--outline-variant)] pb-1">
                <h2 className="text-lg font-semibold text-[var(--on-surface)]">
                  G-code Revisions
                </h2>
                <button
                  onClick={() => {
                    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                    setAddRevisionOpen(true);
                  }}
                  disabled={!auth.isAuthenticated}
                  title={auth.blockReason ?? "Add G-code revision"}
                  className="inline-flex items-center gap-1.5 rounded border border-[var(--outline-variant)] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container-low)] disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add
                </button>
              </div>
              <div className="space-y-3">
                {gcodeFiles.length === 0 && (
                  <p className="font-mono text-xs text-[var(--on-surface-variant)]">
                    No sliced G-code revisions yet.
                  </p>
                )}
                {gcodeFiles.map((f) => {
                  const isEditingRevision = editingRevisionId === f.id;
                  const fileMeta = f.metadata;
                  const uploadedTo = printerFilesByFileId.get(f.id) ?? [];
                  return (
                    <div key={f.id} className="p-3 border border-[var(--primary)]/30 bg-[var(--primary-fixed)]/15 rounded space-y-3">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="flex flex-wrap items-center gap-1.5 mb-1">
                            <span className="font-mono text-[11px] text-[var(--primary)] font-bold uppercase tracking-wider">
                              Rev {f.gcode_revision_number ?? f.version}
                            </span>
                            {f.revision_label && (
                              <span className="border border-[var(--outline-variant)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                                {f.revision_label}
                              </span>
                            )}
                            <span className={`border rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${revisionStatusClass(f.revision_status)}`}>
                              {revisionStatusLabel(f.revision_status)}
                            </span>
                            {f.is_recommended && (
                              <span className="inline-flex items-center gap-1 border border-[var(--primary)]/30 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">
                                <Star className="h-3 w-3 fill-current" /> Recommended
                              </span>
                            )}
                            {uploadedTo.map((row) => (
                              <span key={`${row.printer_id}-${row.remote_filename}`} className="inline-flex items-center gap-1 border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">
                                <Wifi className="h-3 w-3" /> {row.printer_name}
                              </span>
                            ))}
                          </div>
                          <p className="text-sm text-[var(--on-surface)] font-medium truncate">
                            {f.original_filename}
                          </p>
                          <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                            {formatBytes(f.size_bytes)} · {timeAgo(f.uploaded_at)}
                            {fileMeta?.layer_height_mm ? ` · ${fileMeta.layer_height_mm}mm` : ""}
                            {fileMeta?.material_type ? ` · ${fileMeta.material_type}` : ""}
                            {fileMeta?.estimated_time_s ? ` · ${formatDuration(fileMeta.estimated_time_s)}` : ""}
                          </p>
                          {f.revision_notes && !isEditingRevision && (
                            <p className="mt-2 text-xs text-[var(--on-surface-variant)] leading-relaxed">
                              {f.revision_notes}
                            </p>
                          )}
                        </div>
                        <div className="flex items-center gap-1 shrink-0">
                          <button
                            onClick={() => startRevisionEdit(f)}
                            disabled={!auth.isAuthenticated}
                            title={auth.blockReason ?? "Edit revision"}
                            className="text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            <Pencil className="h-4 w-4" />
                          </button>
                          <a
                            href={getAssetUrl(`/api/v1/files/${f.id}/download`)}
                            download={f.original_filename}
                            className="text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors"
                          >
                            <Download className="h-4 w-4" />
                          </a>
                        </div>
                      </div>

                      {isEditingRevision && (
                        <div className="space-y-2 border-t border-[var(--outline-variant)] pt-3">
                          <input
                            value={revisionLabel}
                            onChange={(e) => setRevisionLabel(e.target.value)}
                            maxLength={128}
                            placeholder="Revision label"
                            className="w-full bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded px-3 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                          />
                          <select
                            value={revisionStatus}
                            onChange={(e) => setRevisionStatus(e.target.value as FileRevisionStatus | "")}
                            className="w-full bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded px-3 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                          >
                            <option value="">Unmarked</option>
                            <option value="known_good">Known good</option>
                            <option value="needs_test">Needs test</option>
                            <option value="failed">Failed</option>
                            <option value="archived">Archived</option>
                          </select>
                          <textarea
                            value={revisionNotes}
                            onChange={(e) => setRevisionNotes(e.target.value)}
                            rows={2}
                            placeholder="Notes about print outcome, fit, filament, or what to try next"
                            className="w-full bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded px-3 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)] resize-none"
                          />
                          <label className="flex items-center gap-2 text-xs font-mono text-[var(--on-surface-variant)]">
                            <input
                              type="checkbox"
                              checked={revisionRecommended}
                              onChange={(e) => setRevisionRecommended(e.target.checked)}
                              className="rounded"
                            />
                            Mark as recommended G-code for this model
                          </label>
                          <div className="flex gap-2">
                            <button
                              onClick={() => setEditingRevisionId(null)}
                              disabled={revisionSaving === f.id}
                              className="flex-1 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-xs uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors disabled:opacity-50"
                            >
                              Cancel
                            </button>
                            <button
                              onClick={() => saveRevision(f)}
                              disabled={revisionSaving === f.id}
                              className="flex-1 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5"
                            >
                              {revisionSaving === f.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                              {revisionSaving === f.id ? "Saving..." : "Save"}
                            </button>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>

            {gcodeFiles.length >= 2 && compareLeft && compareRight && (
              <section>
                <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)] flex items-center gap-2">
                  <GitCompare className="h-4 w-4" /> Compare Revisions
                </h2>
                <div className="space-y-3">
                  <div className="grid grid-cols-2 gap-2">
                    <select
                      value={compareLeft?.id ?? ""}
                      onChange={(e) => setCompareLeftId(Number(e.target.value))}
                      className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded px-2 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                    >
                      {gcodeFiles.map((f) => (
                        <option key={f.id} value={f.id}>Rev {f.gcode_revision_number ?? f.version}</option>
                      ))}
                    </select>
                    <select
                      value={compareRight?.id ?? ""}
                      onChange={(e) => setCompareRightId(Number(e.target.value))}
                      className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded px-2 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
                    >
                      {gcodeFiles.map((f) => (
                        <option key={f.id} value={f.id}>Rev {f.gcode_revision_number ?? f.version}</option>
                      ))}
                    </select>
                  </div>
                  <RevisionCompare left={compareLeft} right={compareRight} />
                </div>
              </section>
            )}

            {sourceFiles.length > 0 && (
              <section>
                <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
                  Source Files
                </h2>
                <div className="space-y-2">
                  {sourceFiles.map((f) => (
                    <div key={f.id} className="flex items-center justify-between p-3 border border-[var(--outline-variant)] rounded hover:border-[var(--primary)] hover:shadow-sm transition-all group bg-[var(--surface)]">
                      <div className="flex items-center gap-3 min-w-0">
                        <FileText className="h-5 w-5 flex-shrink-0 text-[var(--outline)] group-hover:text-[var(--primary)]" />
                        <div className="min-w-0">
                          <p className="text-sm text-[var(--on-surface)] font-medium truncate">
                            {f.original_filename}
                          </p>
                          <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                            {formatBytes(f.size_bytes)} · v{f.version} · Source
                          </p>
                        </div>
                      </div>
                      <a
                        href={getAssetUrl(`/api/v1/files/${f.id}/download`)}
                        download={f.original_filename}
                        className="text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors flex-shrink-0"
                      >
                        <Download className="h-5 w-5" />
                      </a>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>

          {/* Klipper Sync Panel */}
          <div className="p-4 md:p-6 border-t border-[var(--outline-variant)] bg-[var(--surface-container-low)] shrink-0 space-y-3">
            {hasGcode && (
              <SendToButtons modelId={model.id} gcodeFiles={gcodeFiles} printerFiles={printerFiles} />
            )}
            {!hasGcode && (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">
                    Sync status
                  </span>
                  <div className="flex items-center gap-1.5 px-2 py-1 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded">
                    <Wifi className="h-3 w-3 text-[var(--on-surface-variant)]" />
                    <span className="font-mono text-xs text-[var(--on-surface-variant)]">
                      No G-code file
                    </span>
                  </div>
                </div>
              </div>
            )}
            <div className="flex items-center justify-between border-t border-[var(--surface-container-highest)] pt-3">
              <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">Files</span>
              <span className="font-mono text-sm text-[var(--on-surface)] font-semibold">{model.files.length}</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">Created</span>
              <span className="font-mono text-xs text-[var(--on-surface)]">{new Date(model.created_at).toLocaleDateString()}</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AddGcodeRevisionModal({
  modelId,
  onClose,
  onUploaded,
}: {
  modelId: number;
  onClose: () => void;
  onUploaded: (model: ModelRead) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [recommended, setRecommended] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || submitting) return;
    const taskId = createTask({
      title: `Upload revision ${file.name}`,
      detail: "Uploading G-code revision",
      status: "running",
      progress: 20,
    });
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (label.trim()) form.append("revision_label", label.trim());
      if (notes.trim()) form.append("revision_notes", notes.trim());
      form.append("revision_status", "needs_test");
      form.append("is_recommended", String(recommended));
      updateTask(taskId, {
        detail: "Adding revision to model",
        status: "running",
        progress: 70,
      });
      onUploaded(await addGcodeRevision(modelId, form));
      updateTask(taskId, {
        detail: "Revision uploaded",
        status: "completed",
        progress: 100,
      });
    } catch (e: any) {
      setError(e.message);
      updateTask(taskId, {
        detail: e.message || "Revision upload failed",
        status: "failed",
        progress: 100,
      });
      toast.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
      <form
        onSubmit={submit}
        className="relative w-full max-w-md rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-5 shadow-lg space-y-4"
      >
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-lg font-semibold text-[var(--on-surface)]">
            Add G-code revision
          </h3>
          <button type="button" onClick={onClose} className="rounded p-1 text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]">
            <X className="h-5 w-5" />
          </button>
        </div>
        {error && (
          <div className="rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-2 text-xs text-[var(--error)]">
            {error}
          </div>
        )}
        <input
          type="file"
          accept=".gcode,.g,.gco"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="w-full rounded border border-[var(--outline-variant)] bg-[var(--surface)] px-3 py-2 font-mono text-xs text-[var(--on-surface)]"
        />
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={128}
          placeholder="Revision label"
          className="w-full rounded border border-[var(--outline-variant)] bg-[var(--surface)] px-3 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
        />
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="What changed in this slice?"
          className="w-full rounded border border-[var(--outline-variant)] bg-[var(--surface)] px-3 py-2 font-mono text-xs text-[var(--on-surface)] resize-none focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
        />
        <label className="flex items-center gap-2 font-mono text-xs text-[var(--on-surface-variant)]">
          <input
            type="checkbox"
            checked={recommended}
            onChange={(e) => setRecommended(e.target.checked)}
            className="rounded"
          />
          Mark as recommended
        </label>
        <div className="flex gap-2">
          <button type="button" onClick={onClose} disabled={submitting} className="flex-1 rounded border border-[var(--outline-variant)] py-2 font-mono text-xs uppercase tracking-wider text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] disabled:opacity-50">
            Cancel
          </button>
          <button type="submit" disabled={!file || submitting} className="flex-1 rounded bg-[var(--primary)] py-2 font-mono text-xs uppercase tracking-wider text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-1.5">
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {submitting ? "Adding..." : "Add revision"}
          </button>
        </div>
      </form>
    </div>
  );
}

function RevisionCompare({ left, right }: { left: FileRead; right: FileRead }) {
  const leftSlicer =
    [left.metadata?.slicer_name, left.metadata?.slicer_version]
      .filter(Boolean)
      .join(" ") || "—";
  const rightSlicer =
    [right.metadata?.slicer_name, right.metadata?.slicer_version]
      .filter(Boolean)
      .join(" ") || "—";
  const rows = [
    [
      "Status",
      revisionStatusLabel(left.revision_status),
      revisionStatusLabel(right.revision_status),
    ],
    [
      "Layer height",
      formatMillimeters(left.metadata?.layer_height_mm),
      formatMillimeters(right.metadata?.layer_height_mm),
    ],
    [
      "First layer",
      formatMillimeters(left.metadata?.first_layer_height_mm),
      formatMillimeters(right.metadata?.first_layer_height_mm),
    ],
    [
      "Nozzle",
      formatMillimeters(left.metadata?.nozzle_diameter_mm),
      formatMillimeters(right.metadata?.nozzle_diameter_mm),
    ],
    [
      "Infill",
      formatPercent(left.metadata?.infill_percent),
      formatPercent(right.metadata?.infill_percent),
    ],
    [
      "Walls",
      left.metadata?.wall_loops ? String(left.metadata.wall_loops) : "—",
      right.metadata?.wall_loops ? String(right.metadata.wall_loops) : "—",
    ],
    [
      "Top / bottom",
      left.metadata?.top_shell_layers || left.metadata?.bottom_shell_layers
        ? `${left.metadata?.top_shell_layers ?? "—"} / ${left.metadata?.bottom_shell_layers ?? "—"}`
        : "—",
      right.metadata?.top_shell_layers || right.metadata?.bottom_shell_layers
        ? `${right.metadata?.top_shell_layers ?? "—"} / ${right.metadata?.bottom_shell_layers ?? "—"}`
        : "—",
    ],
    [
      "Supports",
      left.metadata?.support_material === null || left.metadata?.support_material === undefined
        ? "—"
        : left.metadata.support_material
          ? "Yes"
          : "No",
      right.metadata?.support_material === null || right.metadata?.support_material === undefined
        ? "—"
        : right.metadata.support_material
          ? "Yes"
          : "No",
    ],
    [
      "Nozzle temp",
      formatTemperature(left.metadata?.nozzle_temperature_c),
      formatTemperature(right.metadata?.nozzle_temperature_c),
    ],
    [
      "Bed temp",
      formatTemperature(left.metadata?.bed_temperature_c),
      formatTemperature(right.metadata?.bed_temperature_c),
    ],
    ["Material", left.metadata?.material_type ?? "—", right.metadata?.material_type ?? "—"],
    ["Filament profile", left.metadata?.material_brand ?? "—", right.metadata?.material_brand ?? "—"],
    [
      "Filament",
      formatGrams(left.metadata?.filament_weight_g),
      formatGrams(right.metadata?.filament_weight_g),
    ],
    [
      "Filament cost",
      formatCost(left.metadata?.filament_cost),
      formatCost(right.metadata?.filament_cost),
    ],
    [
      "Est. time",
      formatDuration(left.metadata?.estimated_time_s ?? null),
      formatDuration(right.metadata?.estimated_time_s ?? null),
    ],
    ["Printer", left.metadata?.printer_model ?? "—", right.metadata?.printer_model ?? "—"],
    ["Slicer", leftSlicer, rightSlicer],
    ["Size", formatBytes(left.size_bytes), formatBytes(right.size_bytes)],
    ["SHA-256", left.sha256.slice(0, 12), right.sha256.slice(0, 12)],
  ];

  return (
    <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded overflow-hidden">
      <div className="grid grid-cols-[1fr_1fr_1fr] border-b border-[var(--outline-variant)] bg-[var(--surface-container-low)]">
        <span className="px-2 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">Field</span>
        <span className="px-2 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface)]">Rev {left.gcode_revision_number ?? left.version}</span>
        <span className="px-2 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface)]">Rev {right.gcode_revision_number ?? right.version}</span>
      </div>
      {rows.map(([label, leftValue, rightValue], index) => (
        <div
          key={label}
          className={`grid grid-cols-[1fr_1fr_1fr] ${index === rows.length - 1 ? "" : "border-b border-[var(--surface-container-high)]"}`}
        >
          <span className="px-2 py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">{label}</span>
          <span className="px-2 py-2 font-mono text-[11px] text-[var(--on-surface)] break-words">{leftValue}</span>
          <span className={`px-2 py-2 font-mono text-[11px] break-words ${leftValue === rightValue ? "text-[var(--on-surface)]" : "text-[var(--primary)] font-semibold"}`}>
            {rightValue}
          </span>
        </div>
      ))}
    </div>
  );
}

function SendToButtons({
  modelId,
  gcodeFiles,
  printerFiles,
}: {
  modelId: number;
  gcodeFiles: Pick<FileRead, "id" | "original_filename" | "version" | "gcode_revision_number" | "revision_label" | "is_recommended">[];
  printerFiles: ModelPrinterFileRead[];
}) {
  const auth = useRequireAuth();
  const [showSend, setShowSend] = useState(false);
  const defaultFile = gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1];
  const [selectedFile, setSelectedFile] = useState<number>(defaultFile?.id ?? 0);
  const [startPrint, setStartPrint] = useState(false);
  const [printers, setPrinters] = useState<PrinterRead[]>([]);
  const [selectedPrinterIds, setSelectedPrinterIds] = useState<number[]>([]);
  const [printersLoading, setPrintersLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setPrintersLoading(true);
    listPrinters()
      .then((p) => {
        if (!alive) return;
        setPrinters(p);
        setSelectedPrinterIds((current) => {
          const capableIds = p
            .filter((printer) => printer.capabilities.can_upload)
            .map((printer) => printer.id);
          if (capableIds.length === 0) return [];
          const kept = current.filter((id) => capableIds.includes(id));
          return kept.length > 0 ? kept : [capableIds[0]];
        });
      })
      .catch((e) => {
        if (!alive) return;
        const message =
          e instanceof Error ? e.message : "Failed to load printers";
        setError(message);
      })
      .finally(() => alive && setPrintersLoading(false));
    return () => {
      alive = false;
    };
  }, [showSend]);

  const selectedPrinters = useMemo(
    () => printers.filter((printer) => selectedPrinterIds.includes(printer.id)),
    [printers, selectedPrinterIds],
  );
  const availablePrinters = useMemo(
    () => printers.filter((printer) => printer.capabilities.can_upload),
    [printers],
  );
  const onlineCount = selectedPrinters.filter(
    (printer) => printer.status !== "offline" && printer.status !== "unknown",
  ).length;
  const selectedUploads = printerFiles.filter(
    (row) =>
      row.file_id === selectedFile &&
      selectedPrinterIds.includes(row.printer_id) &&
      !row.missing_since,
  );

  function togglePrinter(id: number) {
    setSelectedPrinterIds((current) =>
      current.includes(id)
        ? current.filter((currentId) => currentId !== id)
        : [...current, id],
    );
  }

  async function send() {
    if (!selectedFile || selectedPrinters.length === 0) return;
    const file = gcodeFiles.find((candidate) => candidate.id === selectedFile);
    const taskId = createTask({
      title: `Send ${file?.original_filename ?? "G-code"}`,
      detail: `Sending to ${selectedPrinters.length} printer${selectedPrinters.length === 1 ? "" : "s"}`,
      status: "running",
      progress: 5,
    });
    setSending(true);
    setError(null);
    try {
      let completed = 0;
      const results = await Promise.allSettled(
        selectedPrinters.map(async (printer) => {
          const job = await sendToPrinter(printer.id, {
            file_id: selectedFile,
            start_print: startPrint,
          });
          completed += 1;
          updateTask(taskId, {
            detail: `${completed}/${selectedPrinters.length} printers completed`,
            status: "running",
            progress: 10 + (completed / selectedPrinters.length) * 85,
          });
          return { printer, job };
        }),
      );

      const successes = results.filter((result) => result.status === "fulfilled");
      const failures = results.filter((result) => result.status === "rejected");

      if (failures.length > 0) {
        const message = `${successes.length}/${selectedPrinters.length} printers succeeded`;
        setError(message);
        updateTask(taskId, {
          detail: message,
          status: successes.length > 0 ? "completed" : "failed",
          progress: 100,
        });
        toast.warning("Some sends failed", message);
      } else {
        updateTask(taskId, {
          detail: startPrint ? "Print started on selected printers" : "Sent to selected printers",
          status: "completed",
          progress: 100,
        });
        setShowSend(false);
        toast.success(
          startPrint
            ? `Print started on ${successes.length} printer${successes.length === 1 ? "" : "s"}`
            : `Sent to ${successes.length} printer${successes.length === 1 ? "" : "s"}`,
        );
      }
    } catch (e: any) {
      const message = e.message || "Send failed";
      setError(message);
      updateTask(taskId, {
        detail: message,
        status: "failed",
        progress: 100,
      });
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <span className="font-mono text-xs text-[var(--on-surface-variant)] uppercase tracking-wider">Klipper status</span>
        <div className="flex items-center gap-1.5 px-2 py-1 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded">
          {printersLoading ? (
            <>
              <Loader2 className="h-3 w-3 animate-spin text-[var(--on-surface-variant)]" />
              <span className="font-mono text-xs text-[var(--on-surface-variant)]">Checking…</span>
            </>
          ) : printers.length === 0 ? (
            <>
              <WifiOff className="h-3 w-3 text-[var(--on-surface-variant)]" />
              <span className="font-mono text-xs text-[var(--on-surface-variant)]">No printers</span>
            </>
          ) : selectedPrinters.length > 0 && onlineCount > 0 ? (
            <>
              <span className="w-2 h-2 rounded-full bg-emerald-500" />
              <span className="font-mono text-xs font-bold text-emerald-500 tracking-wider">
                {onlineCount}/{selectedPrinters.length} online
              </span>
            </>
          ) : (
            <>
              <WifiOff className="h-3 w-3 text-amber-500" />
              <span className="font-mono text-xs text-amber-500 capitalize">
                No selected printer online
              </span>
            </>
          )}
        </div>
      </div>
      {error && !showSend && (
        <div className="rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-2 text-[11px] text-[var(--error)] font-mono break-words">
          {error}
        </div>
      )}

      {showSend ? (
        <div className="space-y-3">
          {printers.length > 0 && (
            <div className="space-y-1.5 rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-2">
              {printers.map((printer) => {
                const disabled = !printer.capabilities.can_upload;
                return (
                  <label
                    key={printer.id}
                    className={`flex items-center justify-between gap-3 rounded px-2 py-1.5 font-mono text-xs ${
                      disabled
                        ? "text-[var(--on-surface-variant)]/60"
                        : "text-[var(--on-surface)] hover:bg-[var(--surface-container-low)]"
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <input
                        type="checkbox"
                        checked={selectedPrinterIds.includes(printer.id)}
                        onChange={() => togglePrinter(printer.id)}
                        disabled={disabled || sending}
                        className="rounded"
                      />
                      <span className="truncate">{printer.name}</span>
                    </span>
                    <span className="shrink-0 text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                      {disabled ? "Unsupported" : printer.status}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
          {availablePrinters.length === 0 && (
            <div className="rounded border border-amber-500/30 bg-amber-500/10 p-2 text-[11px] text-amber-600 font-mono">
              No configured printer supports Vault upload/send.
            </div>
          )}
          <select
            value={selectedFile}
            onChange={(e) => setSelectedFile(Number(e.target.value))}
            className="w-full bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded px-3 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
          >
            {gcodeFiles.map((f) => (
              <option key={f.id} value={f.id}>
                Rev {f.gcode_revision_number ?? f.version}
                {f.revision_label ? `, ${f.revision_label}` : ""}
                {f.is_recommended ? ", recommended" : ""}
              </option>
            ))}
          </select>
          <label className="flex items-center gap-2 text-xs font-mono text-[var(--on-surface-variant)]">
            <input type="checkbox" checked={startPrint} onChange={(e) => setStartPrint(e.target.checked)} className="rounded" />
            Start print immediately
          </label>
          {selectedUploads.length > 0 && (
            <div className="rounded border border-emerald-500/30 bg-emerald-500/10 p-2 text-[11px] text-emerald-600 font-mono break-words">
              Already on{" "}
              {selectedUploads
                .map((upload) => `${upload.printer_name} as ${upload.remote_filename}`)
                .join(", ")}
            </div>
          )}
          {error && (
            <div className="rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-2 text-[11px] text-[var(--error)] font-mono break-words">
              {error}
            </div>
          )}
          <div className="flex gap-2">
            <button onClick={() => setShowSend(false)} disabled={sending} className="flex-1 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-xs uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors disabled:opacity-50">Cancel</button>
            <button onClick={send} disabled={sending || selectedPrinters.length === 0} className="flex-1 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5">
              {sending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              {sending ? "Sending…" : startPrint ? "Send & Print" : "Send"}
            </button>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <Link href="/printers" className="w-full py-2 border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors rounded font-mono text-xs uppercase tracking-wider text-center">
            {printers.length === 0 ? "Configure printers" : "Manage printers"}
          </Link>
          <button
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              setShowSend(true);
            }}
            disabled={printers.length === 0 || !auth.isAuthenticated}
            className="w-full py-2.5 bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {!auth.isAuthenticated ? (
              <><Send className="h-4 w-4" /> Sign in to send</>
            ) : (
              <><Send className="h-4 w-4" /> Send to printer</>
            )}
          </button>
        </div>
      )}
    </div>
  );
}

function SettingRow({
  label, value, chip, highlight, last,
}: { label: string; value: string; chip?: boolean; highlight?: boolean; last?: boolean }) {
  return (
    <div className={`flex justify-between items-center px-3 py-2.5 ${last ? "" : "border-b border-[var(--surface-container-high)]"}`}>
      <span className="font-mono text-xs text-[var(--on-surface-variant)] tracking-wider uppercase">{label}</span>
      {chip ? (
        <span className="px-2 py-0.5 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] rounded font-mono text-[11px]">{value}</span>
      ) : (
        <span className={`font-mono text-[13px] ${highlight ? "text-[var(--primary)] font-bold" : "text-[var(--on-surface)]"}`}>{value}</span>
      )}
    </div>
  );
}
