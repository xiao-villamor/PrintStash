"use client";

import { Suspense, useEffect, useMemo, useRef, useState } from "react";
import type { STLViewerControls, ViewerDisplayMode } from "@/components/stl-viewer";
import dynamic from "next/dynamic";
import Link from "next/link";
import {
  CollectionRead,
  FileRead,
  FileRevisionStatus,
  FileRevisionUpdate,
  MetadataRead,
  ModelRead,
  ModelPrinterFileRead,
  ModelPrintJobRead,
  PrinterRead,
  PrintJobState,
  TagRead,
} from "@/types";

const STLViewer = dynamic(
  () => import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
  { ssr: false, loading: () => <Loader2 className="h-8 w-8 animate-spin text-[var(--on-surface-variant)]" /> },
);
import {
  createTag,
  createManualPrintJob,
  deleteModel,
  getAssetUrl,
  getModelPrinterFiles,
  getModelPrintJobs,
  importPrintJobsFromPrinter,
  listCollections,
  listPrinters,
  listTags,
  sendToPrinter,
  updateFileRevision,
  updateModel,
  addGcodeRevision,
} from "@/lib/api";
import {
  DEFAULT_METADATA_PREFERENCES,
  MetadataPreferences,
  readMetadataPreferences,
} from "@/lib/metadata-preferences";
import { toast } from "@/lib/toast";
import { createTask, updateTask } from "@/lib/task-center";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useRouter } from "next/navigation";
import {
  ArrowLeft,
  Axis3d,
  Box,
  Camera,
  Check,
  CheckCircle2,
  ChevronDown,
  Clock,
  Crosshair,
  Download,
  FileText,
  GitCompare,
  Grid3x3,
  Loader2,
  Maximize2,
  Minus,
  Pencil,
  Plus,
  Printer as PrinterIcon,
  RefreshCw,
  RotateCcw,
  Send,
  Star,
  Trash2,
  Wifi,
  WifiOff,
  X,
  XCircle,
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

function headerStatusLabel(status: FileRevisionStatus | null): string {
  return status === "known_good" ? "Printed OK" : revisionStatusLabel(status);
}

type TabKey = "overview" | "settings" | "revisions" | "files" | "history";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "settings", label: "Settings" },
  { key: "revisions", label: "Revisions" },
  { key: "files", label: "Files" },
  { key: "history", label: "History" },
];

type PrintJobTone = "success" | "error" | "progress";

const PRINT_JOB_PRESENTATION: Record<
  PrintJobState,
  { label: string; tone: PrintJobTone }
> = {
  queued: { label: "Queued", tone: "progress" },
  uploading: { label: "Uploading", tone: "progress" },
  started: { label: "Started", tone: "progress" },
  printing: { label: "Printing", tone: "progress" },
  paused: { label: "Paused", tone: "progress" },
  completed: { label: "Success", tone: "success" },
  cancelled: { label: "Cancelled", tone: "error" },
  failed: { label: "Failed", tone: "error" },
};

function printJobToneClass(tone: PrintJobTone): string {
  switch (tone) {
    case "success":
      return "bg-emerald-500/15 text-emerald-600 border-emerald-500/30";
    case "error":
      return "bg-[var(--error-container)]/40 text-[var(--error)] border-[var(--error)]/30";
    default:
      return "bg-amber-500/15 text-amber-600 border-amber-500/30";
  }
}

type PrintSettingRow = {
  label: string;
  value: string;
  chip?: boolean;
  highlight?: boolean;
};

function buildPrintSettingRows(
  meta: MetadataRead | null | undefined,
  preferences: MetadataPreferences,
): PrintSettingRow[] {
  const rows: PrintSettingRow[] = [];

  if (preferences.printer_profile) {
    rows.push({ label: "PRINTER PROFILE", value: meta?.printer_model ?? "—" });
  }

  if (preferences.material) {
    rows.push({
      label: "MATERIAL",
      value: meta?.material_type ?? "—",
      chip: true,
    });
  }

  if (preferences.filament_profile && meta?.material_brand) {
    rows.push({ label: "FILAMENT PROFILE", value: meta.material_brand });
  }

  if (preferences.layer_height) {
    rows.push({ label: "LAYER HEIGHT", value: formatMillimeters(meta?.layer_height_mm) });
  }

  if (preferences.first_layer && meta?.first_layer_height_mm) {
    rows.push({
      label: "FIRST LAYER",
      value: formatMillimeters(meta.first_layer_height_mm),
    });
  }

  if (preferences.nozzle) {
    rows.push({ label: "NOZZLE", value: formatMillimeters(meta?.nozzle_diameter_mm) });
  }

  if (preferences.infill) {
    rows.push({ label: "INFILL", value: formatPercent(meta?.infill_percent) });
  }

  if (preferences.walls && meta?.wall_loops) {
    rows.push({ label: "WALLS", value: String(meta.wall_loops) });
  }

  if (preferences.top_bottom && (meta?.top_shell_layers || meta?.bottom_shell_layers)) {
    rows.push({
      label: "TOP / BOTTOM",
      value: `${meta?.top_shell_layers ?? "—"} / ${meta?.bottom_shell_layers ?? "—"}`,
    });
  }

  if (
    preferences.supports
    && meta?.support_material !== null
    && meta?.support_material !== undefined
  ) {
    rows.push({ label: "SUPPORTS", value: meta.support_material ? "Yes" : "No" });
  }

  if (preferences.nozzle_temp && meta?.nozzle_temperature_c) {
    rows.push({
      label: "NOZZLE TEMP",
      value: formatTemperature(meta.nozzle_temperature_c),
    });
  }

  if (preferences.bed_temp && meta?.bed_temperature_c) {
    rows.push({ label: "BED TEMP", value: formatTemperature(meta.bed_temperature_c) });
  }

  if (preferences.estimated_time) {
    rows.push({
      label: "EST. TIME",
      value: formatDuration(meta?.estimated_time_s ?? null),
      highlight: true,
    });
  }

  if (preferences.filament_weight) {
    rows.push({ label: "FILAMENT", value: formatGrams(meta?.filament_weight_g) });
  }

  if (preferences.filament_cost && meta?.filament_cost) {
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
  const [editCollection, setEditCollection] = useState(model.collection || "");
  const [editTags, setEditTags] = useState<string[]>([...model.tags]);
  const [catOpen, setCatOpen] = useState(false);
  const [tagInput, setTagInput] = useState("");
  const [collections, setCollections] = useState<CollectionRead[]>([]);
  const [tags, setTags] = useState<TagRead[]>([]);
  const [catLoaded, setCatLoaded] = useState(false);
  const [metadataPreferences, setMetadataPreferences] = useState<MetadataPreferences>(
    DEFAULT_METADATA_PREFERENCES,
  );
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
  const [printJobs, setPrintJobs] = useState<ModelPrintJobRead[]>([]);
  const [activeTab, setActiveTab] = useState<TabKey>("overview");
  const [displayMode, setDisplayMode] = useState<ViewerDisplayMode>("solid");
  const [showGrid, setShowGrid] = useState(false);
  const [showAxes, setShowAxes] = useState(false);
  const [showBoundingBox, setShowBoundingBox] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const [sendFileId, setSendFileId] = useState<number | undefined>(undefined);
  const viewerControls = useRef<STLViewerControls | null>(null);

  useEffect(() => {
    getModelPrinterFiles(model.id).then(setPrinterFiles).catch(() => {});
    getModelPrintJobs(model.id).then(setPrintJobs).catch(() => {});
  }, [model.id]);

  useEffect(() => {
    setMetadataPreferences(readMetadataPreferences());
  }, []);

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
    setEditCollection(model.collection || "");
    setEditTags([...model.tags]);
    setTagInput("");
    setCatOpen(false);
    if (!catLoaded) {
      listCollections().then((c) => { setCollections(c); setCatLoaded(true); }).catch(() => {});
      listTags().then(setTags).catch(() => {});
    }
    setActiveTab("overview");
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
        collection: editCollection || undefined,
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

  function editToggleTag(name: string) {
    const lower = name.toLowerCase();
    setEditTags((p) =>
      p.map((n) => n.toLowerCase()).includes(lower)
        ? p.filter((s) => s.toLowerCase() !== lower)
        : [...p, name],
    );
  }

  async function editCreateTag(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    const existing = tags.find(
      (t) => t.name.toLowerCase() === trimmed.toLowerCase(),
    );
    if (existing) {
      if (!editTags.map((n) => n.toLowerCase()).includes(existing.name.toLowerCase())) {
        editToggleTag(existing.name);
      }
      return;
    }
    try {
      const t = await createTag({ name: trimmed });
      setTags((p) => [...p, t]);
      setEditTags((p) => [...p, t.name]);
    } catch {
      /* ignored */
    }
  }

  const editFilteredTags = useMemo(() => {
    const q = tagInput.toLowerCase().trim();
    const selectedNames = editTags.map((n) => n.toLowerCase());
    return tags.filter(
      (t) =>
        !selectedNames.includes(t.name.toLowerCase()) &&
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
  const printSettingRows = buildPrintSettingRows(meta, metadataPreferences);
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

  async function markRevision(file: FileRead, patch: FileRevisionUpdate) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setRevisionSaving(file.id);
    try {
      const updated = await updateFileRevision(model.id, file.id, patch);
      setModel(updated);
      toast.success("Revision updated");
    } catch (e) {
      toast.error(e);
    } finally {
      setRevisionSaving(null);
    }
  }

  function requestSend(fileId: number) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setSendFileId(fileId);
    setSendOpen(true);
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
          <div className="min-w-0">
            {editing ? (
              <input
                value={editName}
                onChange={(e) => setEditName(e.target.value)}
                className="w-full bg-[var(--surface)] text-[var(--on-surface)] font-mono text-lg border border-[var(--outline-variant)] rounded px-2 py-0.5 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                placeholder="Model name"
              />
            ) : (
              <h1 className="text-xl font-semibold text-[var(--on-surface)] leading-tight truncate">
                {model.name}
              </h1>
            )}
            <span className="font-mono text-[13px] text-[var(--on-surface-variant)]">
              {(meshFile ?? sourceFiles[0]) ? `${(meshFile ?? sourceFiles[0])!.file_type.toUpperCase()} source · ` : ""}
              {gcodeFiles.length} G-code revision{gcodeFiles.length === 1 ? "" : "s"} · Last updated {timeAgo(model.updated_at)}
            </span>
            {!editing && (recommendedGcode || meta?.material_type || meta?.printer_model) && (
              <div className="flex flex-wrap items-center gap-1.5 mt-1.5">
                {recommendedGcode && (
                  <span className="inline-flex items-center gap-1 border border-[var(--primary)]/30 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">
                    <Star className="h-3 w-3 fill-current" /> Recommended Rev {recommendedGcode.gcode_revision_number ?? recommendedGcode.version}
                  </span>
                )}
                {recommendedGcode?.revision_status && (
                  <span className={`border rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${revisionStatusClass(recommendedGcode.revision_status)}`}>
                    {headerStatusLabel(recommendedGcode.revision_status)}
                  </span>
                )}
                {meta?.material_type && (
                  <span className="border border-[var(--outline-variant)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                    {meta.material_type}
                  </span>
                )}
                {meta?.printer_model && (
                  <span className="inline-flex items-center gap-1 border border-[var(--outline-variant)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                    <PrinterIcon className="h-3 w-3" /> {meta.printer_model}
                  </span>
                )}
              </div>
            )}
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
                displayMode={displayMode}
                showGrid={showGrid}
                showAxes={showAxes}
                showBoundingBox={showBoundingBox}
                screenshotName={model.slug || model.name}
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

          {/* Viewer toolbar (top-left) */}
          {meshFile && (
            <ViewerToolbar
              displayMode={displayMode}
              setDisplayMode={setDisplayMode}
              showGrid={showGrid}
              setShowGrid={setShowGrid}
              showAxes={showAxes}
              setShowAxes={setShowAxes}
              showBoundingBox={showBoundingBox}
              setShowBoundingBox={setShowBoundingBox}
              controls={viewerControls}
            />
          )}

          {/* Viewing label (top-right) */}
          {meshFile && (
            <div className="absolute top-4 right-4 z-10 max-w-[60%]">
              <div className="bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded px-2.5 py-1.5 text-right">
                <p className="font-mono text-[11px] text-[var(--on-surface)] truncate">
                  Viewing: {meshFile.original_filename}
                </p>
                <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)]">
                  Source model
                  {recommendedGcode
                    ? ` · Recommended G-code: Rev ${recommendedGcode.gcode_revision_number ?? recommendedGcode.version}`
                    : ""}
                </p>
              </div>
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

          {/* Dimensions overlay (bottom-left) */}
          {meta?.bbox_x_mm && meta?.bbox_y_mm && meta?.bbox_z_mm && (
            <div className="absolute bottom-4 left-4 z-10">
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
        <div className="md:w-[480px] bg-[var(--surface-container-lowest)] border-l-0 md:border-l border-t md:border-t-0 border-[var(--outline-variant)] flex flex-col h-auto md:h-full shrink-0 min-h-0">
          {/* Segmented tab navigation */}
          <nav className="flex shrink-0 border-b border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] px-2 overflow-x-auto scrollbar-none [&::-webkit-scrollbar]:hidden [-ms-overflow-style:none] [scrollbar-width:none]">
            {TABS.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`relative px-3 py-3 font-mono text-[11px] uppercase tracking-wider whitespace-nowrap transition-colors ${
                  activeTab === tab.key
                    ? "text-[var(--primary)]"
                    : "text-[var(--on-surface-variant)] hover:text-[var(--on-surface)]"
                }`}
              >
                {tab.label}
                {tab.key === "revisions" && gcodeFiles.length > 0 && (
                  <span className="ml-1 opacity-60">{gcodeFiles.length}</span>
                )}
                {activeTab === tab.key && (
                  <span className="absolute inset-x-2 -bottom-px h-0.5 bg-[var(--primary)] rounded-full" />
                )}
              </button>
            ))}
          </nav>
          <div className="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 md:space-y-8 [scrollbar-width:thin] [scrollbar-color:var(--outline-variant)_transparent] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[var(--outline-variant)] [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb:hover]:bg-[var(--primary)]/50">
            {/* Overview: recommended print + collection/tags/edit */}
            {activeTab === "overview" && (
              <RecommendedPrintCard
                file={recommendedGcode ?? gcodeFiles[gcodeFiles.length - 1] ?? null}
                hasGcode={hasGcode}
                saving={revisionSaving}
                onSend={requestSend}
                onCompare={() => setActiveTab("revisions")}
                onMark={markRevision}
                onAddRevision={() => {
                  if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                  setAddRevisionOpen(true);
                }}
              />
            )}

            {/* Print Settings */}
            {activeTab === "settings" && (
            <>
            {printSettingRows.length === 0 && (
              <p className="font-mono text-xs text-[var(--on-surface-variant)]">
                No print settings recorded yet. Add a sliced G-code revision to capture them.
              </p>
            )}
            {printSettingRows.length > 0 && (
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
            )}

            {/* Mesh Geometry */}
            {((metadataPreferences.mesh_volume && meta?.volume_mm3)
              || (metadataPreferences.mesh_triangles && meta?.triangle_count)) && (
              <section>
                <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
                  Mesh Geometry
                </h2>
                <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded flex flex-col">
                  {metadataPreferences.mesh_volume && meta?.volume_mm3 && (
                    <SettingRow
                      label="VOLUME"
                      value={meta.volume_mm3 < 1000 ? `${meta.volume_mm3.toFixed(1)} mm³` : `${(meta.volume_mm3 / 1000).toFixed(2)} cm³`}
                      last={!metadataPreferences.mesh_triangles || !meta?.triangle_count}
                    />
                  )}
                  {metadataPreferences.mesh_triangles && meta?.triangle_count && (
                    <SettingRow label="TRIANGLES" value={meta.triangle_count.toLocaleString()} last />
                  )}
                </div>
              </section>
            )}

            {/* Slicer info */}
            {metadataPreferences.slicer_info && meta?.slicer_name && (
              <p className="font-mono text-xs text-[var(--on-surface-variant)]">
                Sliced with {meta.slicer_name}
                {meta.slicer_version ? ` v${meta.slicer_version}` : ""}
              </p>
            )}
            </>
            )}

            {/* Tags & Collection (Overview) */}
            {activeTab === "overview" && (
            editing ? (
              <div className="space-y-4">
                {/* Collection picker */}
                <div>
                  <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
                    Collection
                  </label>
                  <div className="relative">
                    <button
                      type="button"
                      onClick={() => setCatOpen((v) => !v)}
                      className="w-full h-10 flex items-center justify-between bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
                    >
                      <span className={editCollection ? "" : "text-[var(--on-surface-variant)]/60"}>
                        {editCollection || "None"}
                      </span>
                      <ChevronDown className="h-4 w-4 text-[var(--on-surface-variant)]" />
                    </button>
                    {catOpen && (
                      <>
                        <div className="fixed inset-0 z-40" onClick={() => setCatOpen(false)} />
                        <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-56 overflow-y-auto">
                          <button
                            type="button"
                            onClick={() => { setEditCollection(""); setCatOpen(false); }}
                            className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                          >
                            None
                          </button>
                          {collections.map((c) => (
                            <button
                              key={c.id}
                              type="button"
                              onClick={() => { setEditCollection(c.path); setCatOpen(false); }}
                              className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                                editCollection === c.path
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
                            onClick={() => { editToggleTag(t.name); setTagInput(""); }}
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
                      {editTags.map((name) => (
                        <span key={name} className="inline-flex items-center gap-1 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] pl-2 pr-1 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider">
                          {name}
                          <button type="button" onClick={() => editToggleTag(name)} aria-label={`Remove ${name}`} className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-[var(--on-secondary-container)]/10">
                            <X className="h-3 w-3" />
                          </button>
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            ) : (
              <div className="flex flex-wrap gap-2">
                {model.collection && (
                  <span className="bg-[var(--surface-container)] text-[var(--on-surface)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
                    {model.collection}
                  </span>
                )}
                {model.tags.map((t) => (
                  <span key={t} className="bg-[var(--secondary-container)] text-[var(--on-secondary-container)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
                    {t}
                  </span>
                ))}
              </div>
            ))}

            {/* G-code Revisions (Revisions tab) */}
            {activeTab === "revisions" && (
            <>
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
            </>
            )}

            {/* Source Files (Files tab) */}
            {activeTab === "files" && (
              <section>
                <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
                  Source Files
                </h2>
                {sourceFiles.length === 0 && (
                  <p className="font-mono text-xs text-[var(--on-surface-variant)]">
                    No source files (STL / 3MF / OBJ) for this model.
                  </p>
                )}
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

            {/* Print History (History tab) */}
            {activeTab === "history" && (
              <PrintHistorySection
                jobs={printJobs}
                modelId={model.id}
                gcodeFiles={gcodeFiles}
                onJobCreated={(job) => setPrintJobs((p) => [job, ...p])}
              />
            )}
          </div>

          {/* Klipper Sync Panel */}
          <div className="p-4 md:p-6 border-t border-[var(--outline-variant)] bg-[var(--surface-container-low)] shrink-0 space-y-3">
            {hasGcode && (
              <SendToButtons
                modelId={model.id}
                gcodeFiles={gcodeFiles}
                printerFiles={printerFiles}
                open={sendOpen}
                onOpenChange={setSendOpen}
                preselectFileId={sendFileId}
              />
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
  open,
  onOpenChange,
  preselectFileId,
}: {
  modelId: number;
  gcodeFiles: Pick<FileRead, "id" | "original_filename" | "version" | "gcode_revision_number" | "revision_label" | "is_recommended">[];
  printerFiles: ModelPrinterFileRead[];
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  preselectFileId?: number;
}) {
  const auth = useRequireAuth();
  const [internalOpen, setInternalOpen] = useState(false);
  const showSend = open ?? internalOpen;
  const setShowSend = onOpenChange ?? setInternalOpen;
  const defaultFile = gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1];
  const [selectedFile, setSelectedFile] = useState<number>(defaultFile?.id ?? 0);

  useEffect(() => {
    if (showSend && preselectFileId) setSelectedFile(preselectFileId);
  }, [showSend, preselectFileId]);
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
      ) : printers.length === 0 ? (
        <div className="space-y-2 rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-3">
          <div className="flex items-center gap-2">
            <WifiOff className="h-4 w-4 text-[var(--on-surface-variant)]" />
            <span className="font-mono text-xs uppercase tracking-wider text-[var(--on-surface)]">
              No printers configured
            </span>
          </div>
          <p className="font-mono text-[11px] text-[var(--on-surface-variant)] leading-relaxed">
            Connect Klipper / Moonraker to send files directly to a printer.
          </p>
          <Link
            href="/printers"
            className="mt-1 w-full py-2 bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2"
          >
            <PrinterIcon className="h-4 w-4" /> Configure printer
          </Link>
        </div>
      ) : (
        <div className="flex flex-col gap-2">
          <button
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              setShowSend(true);
            }}
            disabled={!auth.isAuthenticated}
            className="w-full py-2.5 bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {!auth.isAuthenticated ? (
              <><Send className="h-4 w-4" /> Sign in to send</>
            ) : (
              <><Send className="h-4 w-4" /> Send to printer</>
            )}
          </button>
          <Link href="/printers" className="w-full py-2 border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] transition-colors rounded font-mono text-xs uppercase tracking-wider text-center">
            Manage printers
          </Link>
        </div>
      )}
    </div>
  );
}

function ViewerToolbar({
  displayMode,
  setDisplayMode,
  showGrid,
  setShowGrid,
  showAxes,
  setShowAxes,
  showBoundingBox,
  setShowBoundingBox,
  controls,
}: {
  displayMode: ViewerDisplayMode;
  setDisplayMode: (m: ViewerDisplayMode) => void;
  showGrid: boolean;
  setShowGrid: (v: boolean) => void;
  showAxes: boolean;
  setShowAxes: (v: boolean) => void;
  showBoundingBox: boolean;
  setShowBoundingBox: (v: boolean) => void;
  controls: React.RefObject<STLViewerControls | null>;
}) {
  const modes: { key: ViewerDisplayMode; label: string }[] = [
    { key: "solid", label: "Solid" },
    { key: "xray", label: "X-Ray" },
    { key: "wireframe", label: "Wire" },
  ];
  const cluster =
    "flex bg-[var(--surface-container-lowest)]/90 backdrop-blur border border-[var(--outline-variant)] rounded overflow-hidden shadow-sm";
  const iconBtn =
    "w-9 h-9 flex items-center justify-center text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] hover:text-[var(--primary)] transition-colors";

  return (
    <div className="absolute top-4 left-4 z-10 flex flex-wrap items-center gap-1.5">
      <div className={cluster}>
        {modes.map((m) => (
          <button
            key={m.key}
            onClick={() => setDisplayMode(m.key)}
            className={`px-2.5 h-9 font-mono text-[11px] uppercase tracking-wider transition-colors ${
              displayMode === m.key
                ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
            }`}
            title={`${m.label} view`}
          >
            {m.label}
          </button>
        ))}
      </div>
      <div className={cluster}>
        <button onClick={() => controls.current?.fit()} className={`${iconBtn} border-r border-[var(--outline-variant)]`} title="Fit view">
          <Maximize2 className="h-4 w-4" />
        </button>
        <button onClick={() => controls.current?.center()} className={`${iconBtn} border-r border-[var(--outline-variant)]`} title="Center">
          <Crosshair className="h-4 w-4" />
        </button>
        <button onClick={() => controls.current?.screenshot()} className={iconBtn} title="Screenshot">
          <Camera className="h-4 w-4" />
        </button>
      </div>
      <div className={cluster}>
        <button
          onClick={() => setShowGrid(!showGrid)}
          className={`${iconBtn} border-r border-[var(--outline-variant)] ${showGrid ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
          title="Build plate grid"
        >
          <Grid3x3 className="h-4 w-4" />
        </button>
        <button
          onClick={() => setShowAxes(!showAxes)}
          className={`${iconBtn} border-r border-[var(--outline-variant)] ${showAxes ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
          title="XYZ axes"
        >
          <Axis3d className="h-4 w-4" />
        </button>
        <button
          onClick={() => setShowBoundingBox(!showBoundingBox)}
          className={`${iconBtn} ${showBoundingBox ? "text-[var(--primary)] bg-[var(--secondary-container)]" : ""}`}
          title="Bounding box"
        >
          <Box className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function RecommendedPrintCard({
  file,
  hasGcode,
  saving,
  onSend,
  onCompare,
  onMark,
  onAddRevision,
}: {
  file: FileRead | null;
  hasGcode: boolean;
  saving: number | null;
  onSend: (fileId: number) => void;
  onCompare: () => void;
  onMark: (file: FileRead, patch: FileRevisionUpdate) => void;
  onAddRevision: () => void;
}) {
  const meta = file?.metadata;
  const isSaving = file ? saving === file.id : false;

  const rows: PrintSettingRow[] = file
    ? [
        { label: "PRINTER", value: meta?.printer_model ?? "—" },
        { label: "MATERIAL", value: meta?.material_type ?? "—", chip: true },
        { label: "LAYER HEIGHT", value: formatMillimeters(meta?.layer_height_mm) },
        { label: "EST. TIME", value: formatDuration(meta?.estimated_time_s ?? null), highlight: true },
        { label: "FILAMENT", value: formatGrams(meta?.filament_weight_g) },
        {
          label: "SLICER",
          value:
            [meta?.slicer_name, meta?.slicer_version].filter(Boolean).join(" ") || "—",
        },
      ]
    : [];

  return (
    <section>
      <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)] flex items-center gap-2">
        <Star className="h-4 w-4 text-[var(--primary)]" /> Recommended Print
      </h2>

      {!file ? (
        <div className="rounded border border-[var(--outline-variant)] bg-[var(--surface)] p-4 space-y-3">
          <p className="font-mono text-xs text-[var(--on-surface-variant)] leading-relaxed">
            {hasGcode
              ? "No revision is marked as recommended yet. Mark a known-good G-code as recommended."
              : "No sliced G-code yet. Add a revision to capture the settings that worked."}
          </p>
          <button
            onClick={onAddRevision}
            className="w-full py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-xs uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors flex items-center justify-center gap-1.5"
          >
            <Plus className="h-4 w-4" /> Add G-code revision
          </button>
        </div>
      ) : (
        <div className="rounded border border-[var(--primary)]/30 bg-[var(--primary-fixed)]/15 p-3 space-y-3">
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="font-mono text-[11px] text-[var(--primary)] font-bold uppercase tracking-wider">
              Rev {file.gcode_revision_number ?? file.version}
            </span>
            <span className={`border rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${revisionStatusClass(file.revision_status)}`}>
              {headerStatusLabel(file.revision_status)}
            </span>
            {file.is_recommended && (
              <span className="inline-flex items-center gap-1 border border-[var(--primary)]/30 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider">
                <Star className="h-3 w-3 fill-current" /> Recommended
              </span>
            )}
          </div>
          <p className="text-sm text-[var(--on-surface)] font-medium truncate">
            {file.original_filename}
          </p>
          <div className="bg-[var(--surface)] border border-[var(--outline-variant)] rounded flex flex-col">
            {rows.map((row, index) => (
              <SettingRow
                key={row.label}
                label={row.label}
                value={row.value}
                chip={row.chip}
                highlight={row.highlight}
                last={index === rows.length - 1}
              />
            ))}
          </div>

          <button
            onClick={() => onSend(file.id)}
            className="w-full py-2.5 bg-[var(--primary)] text-[var(--primary-foreground)] hover:opacity-90 transition-opacity rounded font-mono text-xs uppercase tracking-wider shadow-sm flex items-center justify-center gap-2"
          >
            <Send className="h-4 w-4" /> Send to printer
          </button>
          <div className="grid grid-cols-2 gap-2">
            <a
              href={getAssetUrl(`/api/v1/files/${file.id}/download`)}
              download={file.original_filename}
              className="py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-[11px] uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors flex items-center justify-center gap-1.5"
            >
              <Download className="h-4 w-4" /> Download
            </a>
            <button
              onClick={onCompare}
              className="py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-[11px] uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors flex items-center justify-center gap-1.5"
            >
              <GitCompare className="h-4 w-4" /> Compare
            </button>
            <button
              onClick={() => onMark(file, { revision_status: "failed" })}
              disabled={isSaving}
              className="py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-[11px] uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50"
            >
              <XCircle className="h-4 w-4" /> Mark failed
            </button>
            <button
              onClick={() => onMark(file, { is_recommended: true, revision_status: "known_good" })}
              disabled={isSaving || file.is_recommended}
              className="py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-[11px] uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors flex items-center justify-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Star className="h-4 w-4" />} Recommend
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

type PrintHistoryMode = "manual" | "auto";

function PrintHistorySection({
  jobs,
  modelId,
  gcodeFiles,
  onJobCreated,
}: {
  jobs: ModelPrintJobRead[];
  modelId: number;
  gcodeFiles: FileRead[];
  onJobCreated: (job: ModelPrintJobRead) => void;
}) {
  const [showAdd, setShowAdd] = useState(false);
  const [mode, setMode] = useState<PrintHistoryMode>("manual");

  // Manual form state
  const [printers, setPrinters] = useState<PrinterRead[]>([]);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | "">("");
  const [selectedFileId, setSelectedFileId] = useState<number | "">(gcodeFiles[0]?.id ?? "");
  const [jobState, setJobState] = useState("completed");
  const [startedAt, setStartedAt] = useState("");
  const [finishedAt, setFinishedAt] = useState("");
  const [jobError, setJobError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  // Auto mode state
  const [importing, setImporting] = useState(false);
  const [importResults, setImportResults] = useState<{ filename: string; imported: boolean }[]>([]);
  const [importDone, setImportDone] = useState(false);

  function openAdd() {
    setShowAdd(true);
    setMode("manual");
    setSelectedPrinterId("");
    setSelectedFileId(gcodeFiles[0]?.id ?? "");
    setJobState("completed");
    setStartedAt("");
    setFinishedAt("");
    setJobError("");
    setImportResults([]);
    setImportDone(false);
    if (printers.length === 0) {
      listPrinters().then(setPrinters).catch(() => {});
    }
  }

  async function submitManual() {
    if (!selectedPrinterId || !selectedFileId) return;
    setSubmitting(true);
    try {
      const job = await createManualPrintJob(modelId, {
        printer_id: selectedPrinterId as number,
        file_id: selectedFileId as number,
        state: jobState,
        started_at: startedAt || null,
        finished_at: finishedAt || null,
        error: jobError || null,
      });
      onJobCreated(job);
      setShowAdd(false);
      toast.success("Print record added");
    } catch (e) {
      toast.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  async function runAutoImport() {
    if (!selectedPrinterId) return;
    setImporting(true);
    setImportResults([]);
    setImportDone(false);
    try {
      const results = await importPrintJobsFromPrinter(modelId, selectedPrinterId as number);
      setImportResults(results.map((r) => ({ filename: r.filename, imported: r.imported })));
      setImportDone(true);
      const imported = results.filter((r) => r.imported).length;
      if (imported > 0) {
        const refreshed = await getModelPrintJobs(modelId);
        refreshed.forEach((j) => onJobCreated(j));
        toast.success(`Imported ${imported} job${imported === 1 ? "" : "s"} from printer`);
      } else {
        toast.success("No new jobs to import");
      }
    } catch (e) {
      toast.error(e);
    } finally {
      setImporting(false);
    }
  }

  return (
    <section>
      <div className="flex items-center justify-between mb-4 pb-1 border-b border-[var(--outline-variant)]">
        <h2 className="text-lg font-semibold text-[var(--on-surface)] flex items-center gap-2">
          <Clock className="h-4 w-4" /> Print History
        </h2>
        <button
          onClick={openAdd}
          className="inline-flex items-center gap-1.5 rounded border border-[var(--outline-variant)] px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container-low)]"
        >
          <Plus className="h-3.5 w-3.5" /> Add Record
        </button>
      </div>

      {/* Add record panel */}
      {showAdd && (
        <div className="mb-4 border border-[var(--outline-variant)] rounded bg-[var(--surface-container-low)] p-3 space-y-3">
          {/* Mode toggle */}
          <div className="flex gap-1">
            {(["manual", "auto"] as PrintHistoryMode[]).map((m) => (
              <button
                key={m}
                onClick={() => { setMode(m); setImportResults([]); setImportDone(false); }}
                className={`px-3 py-1 font-mono text-[10px] uppercase tracking-wider rounded transition-colors ${
                  mode === m
                    ? "bg-[var(--primary)] text-[var(--primary-foreground)]"
                    : "border border-[var(--outline-variant)] text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)]"
                }`}
              >
                {m === "manual" ? "Manual Entry" : "Auto from Printer"}
              </button>
            ))}
          </div>

          {mode === "manual" ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Printer</label>
                  <select
                    value={selectedPrinterId}
                    onChange={(e) => setSelectedPrinterId(e.target.value ? Number(e.target.value) : "")}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  >
                    <option value="">Select printer…</option>
                    {printers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                  </select>
                </div>
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">G-code Revision</label>
                  <select
                    value={selectedFileId}
                    onChange={(e) => setSelectedFileId(e.target.value ? Number(e.target.value) : "")}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  >
                    <option value="">Select revision…</option>
                    {gcodeFiles.map((f, i) => (
                      <option key={f.id} value={f.id}>Rev {i + 1} — {f.original_filename}</option>
                    ))}
                  </select>
                </div>
              </div>
              <div>
                <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Result</label>
                <select
                  value={jobState}
                  onChange={(e) => setJobState(e.target.value)}
                  className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                >
                  <option value="completed">Completed</option>
                  <option value="failed">Failed</option>
                  <option value="cancelled">Cancelled</option>
                </select>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Started (opt.)</label>
                  <input
                    type="datetime-local"
                    value={startedAt}
                    onChange={(e) => setStartedAt(e.target.value)}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Finished (opt.)</label>
                  <input
                    type="datetime-local"
                    value={finishedAt}
                    onChange={(e) => setFinishedAt(e.target.value)}
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
              </div>
              {jobState === "failed" && (
                <div>
                  <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Error (opt.)</label>
                  <input
                    value={jobError}
                    onChange={(e) => setJobError(e.target.value)}
                    placeholder="Describe what went wrong…"
                    className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                  />
                </div>
              )}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={submitManual}
                  disabled={submitting || !selectedPrinterId || !selectedFileId}
                  className="flex-1 h-8 bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider rounded disabled:opacity-50 hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
                >
                  {submitting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}
                  Save
                </button>
                <button onClick={() => setShowAdd(false)} className="px-3 h-8 border border-[var(--outline-variant)] rounded font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] transition-colors">
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                Fetch recent print history from a Moonraker printer and import jobs matching this model&apos;s G-code files.
              </p>
              <div>
                <label className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">Printer</label>
                <select
                  value={selectedPrinterId}
                  onChange={(e) => { setSelectedPrinterId(e.target.value ? Number(e.target.value) : ""); setImportResults([]); setImportDone(false); }}
                  className="w-full h-8 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-xs border border-[var(--outline-variant)] rounded px-2 focus:outline-none focus:ring-1 focus:ring-[var(--primary)]"
                >
                  <option value="">Select printer…</option>
                  {printers.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </div>
              {importDone && importResults.length > 0 && (
                <div className="space-y-1">
                  {importResults.map((r) => (
                    <div key={r.filename} className="flex items-center gap-2 font-mono text-[11px]">
                      {r.imported
                        ? <CheckCircle2 className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                        : <XCircle className="h-3.5 w-3.5 text-[var(--on-surface-variant)] shrink-0" />
                      }
                      <span className={r.imported ? "text-[var(--on-surface)]" : "text-[var(--on-surface-variant)]"}>{r.filename}</span>
                      <span className="opacity-50">{r.imported ? "imported" : "already exists"}</span>
                    </div>
                  ))}
                </div>
              )}
              {importDone && importResults.length === 0 && (
                <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">No matching jobs found on this printer.</p>
              )}
              <div className="flex gap-2 pt-1">
                <button
                  onClick={runAutoImport}
                  disabled={importing || !selectedPrinterId}
                  className="flex-1 h-8 bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider rounded disabled:opacity-50 hover:opacity-90 transition-opacity flex items-center justify-center gap-1.5"
                >
                  {importing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                  Fetch &amp; Import
                </button>
                <button onClick={() => setShowAdd(false)} className="px-3 h-8 border border-[var(--outline-variant)] rounded font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-high)] transition-colors">
                  Close
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {jobs.length === 0 ? (
        <p className="font-mono text-xs text-[var(--on-surface-variant)]">
          No print history yet. Add a record manually or import from a printer.
        </p>
      ) : (
        <div className="space-y-2">
          {jobs.map((job) => {
            const present = PRINT_JOB_PRESENTATION[job.state];
            const Icon =
              present.tone === "success" ? CheckCircle2 : present.tone === "error" ? XCircle : Clock;
            return (
              <div
                key={job.id}
                className="p-3 border border-[var(--outline-variant)] rounded bg-[var(--surface)] space-y-1"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <Icon
                      className={`h-4 w-4 shrink-0 ${
                        present.tone === "success"
                          ? "text-emerald-600"
                          : present.tone === "error"
                            ? "text-[var(--error)]"
                            : "text-amber-600"
                      }`}
                    />
                    <span className="font-mono text-[13px] text-[var(--on-surface)] truncate">
                      Rev {job.gcode_revision_number ?? "—"} · {job.printer_name}
                    </span>
                  </div>
                  <span className={`shrink-0 border rounded px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider ${printJobToneClass(present.tone)}`}>
                    {present.label}
                  </span>
                </div>
                <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                  {job.material_type ? `${job.material_type} · ` : ""}
                  {timeAgo(job.created_at)}
                </p>
                {job.error && (
                  <p className="font-mono text-[11px] text-[var(--error)] break-words">
                    {job.error}
                  </p>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
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
