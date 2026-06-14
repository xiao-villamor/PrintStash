"use client";

import { useState } from "react";
import {
  Check,
  Download,
  GitCompare,
  Loader2,
  Pencil,
  Plus,
  Star,
  Wifi,
} from "lucide-react";

import { downloadAuthenticatedFile } from "@/lib/api";
import { formatBytes, formatDuration, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import {
  FileRead,
  FileRevisionStatus,
  ModelPrinterFileRead,
  ModelRead,
} from "@/types";

import { revisionStatusClass, revisionStatusLabel } from "./presentation";
import { RevisionCompare } from "./revision-compare";
import { useRevisionUpdater } from "./use-revision-updater";
import { SlicerOpenButton } from "@/components/slicer-open-button";

export function RevisionsTab({
  modelId,
  gcodeFiles,
  printerFilesByFileId,
  onModel,
  onAddRevision,
}: {
  modelId: number;
  gcodeFiles: FileRead[];
  printerFilesByFileId: Map<number, ModelPrinterFileRead[]>;
  onModel: (model: ModelRead) => void;
  onAddRevision: () => void;
}) {
  const { auth, saving, update } = useRevisionUpdater(modelId, onModel);

  // Revision edit form — local to this tab.
  const [editingRevisionId, setEditingRevisionId] = useState<number | null>(null);
  const [revisionLabel, setRevisionLabel] = useState("");
  const [revisionStatus, setRevisionStatus] = useState<FileRevisionStatus | "">("");
  const [revisionNotes, setRevisionNotes] = useState("");
  const [revisionRecommended, setRevisionRecommended] = useState(false);

  // Compare selection — local to this tab.
  const [compareLeftId, setCompareLeftId] = useState<number>(gcodeFiles.at(-1)?.id ?? 0);
  const [compareRightId, setCompareRightId] = useState<number>(
    gcodeFiles.at(-2)?.id ?? gcodeFiles.at(-1)?.id ?? 0,
  );
  const compareLeft = gcodeFiles.find((f) => f.id === compareLeftId) ?? gcodeFiles[gcodeFiles.length - 1] ?? null;
  const compareRight = gcodeFiles.find((f) => f.id === compareRightId) ?? gcodeFiles[gcodeFiles.length - 2] ?? null;

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
    const ok = await update(file, {
      revision_label: revisionLabel,
      revision_status: revisionStatus || null,
      revision_notes: revisionNotes,
      is_recommended: revisionRecommended,
    });
    if (ok) setEditingRevisionId(null);
  }

  return (
    <>
      <section>
        <div className="mb-4 flex items-center justify-between gap-3 border-b border-[var(--outline-variant)] pb-1">
          <h2 className="text-lg font-semibold text-[var(--on-surface)]">
            G-code Revisions
          </h2>
          <button
            onClick={onAddRevision}
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
                  <div className="flex items-center gap-0.5 shrink-0">
                    <button
                      onClick={() => startRevisionEdit(f)}
                      disabled={!auth.isAuthenticated}
                      title={auth.blockReason ?? "Edit revision"}
                      className="text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <SlicerOpenButton fileId={f.id} size="sm" />
                    <button
                      type="button"
                      onClick={() =>
                        downloadAuthenticatedFile(
                          `/api/v1/files/${f.id}/download`,
                          f.original_filename,
                        ).catch((e) => toast.error(e))
                      }
                      title="Download"
                      className="text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors"
                    >
                      <Download className="h-4 w-4" />
                    </button>
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
                        disabled={saving === f.id}
                        className="flex-1 py-2 rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] font-mono text-xs uppercase tracking-wider hover:bg-[var(--surface-container-low)] transition-colors disabled:opacity-50"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => saveRevision(f)}
                        disabled={saving === f.id}
                        className="flex-1 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5"
                      >
                        {saving === f.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
                        {saving === f.id ? "Saving..." : "Save"}
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
  );
}
