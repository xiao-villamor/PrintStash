"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useState } from "react";
import {
  Check,
  Download,
  GitCompare,
  Loader2,
  Pencil,
  Plus,
  Star,
  Trash2,
  Wifi,
} from "lucide-react";

import {
  batchSetRevisionLabels,
  downloadAuthenticatedFile,
  getArtifactOutcomes,
  getModel,
  replaceFileTags,
} from "@/lib/api";
import { useTags } from "@/lib/queries";
import { formatBytes, formatDuration, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import {
  FileRead,
  ArtifactOutcomeRead,
  FileRevisionStatus,
  ModelPrinterFileRead,
  ModelRead,
} from "@/types";

import { revisionStatusClass, revisionStatusLabel } from "./presentation";
import { RevisionCompare } from "./revision-compare";
import { Localized } from "@/components/ui/localized";
import { useRevisionUpdater } from "./use-revision-updater";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";

const NO_OUTCOMES: ArtifactOutcomeRead[] = [];

/** The revision statuses the edit form offers, in menu order; `""` is unmarked. */
const REVISION_STATUS_CHOICES = ["", "known_good", "needs_test", "failed", "archived"] as const;

type RevisionStatusChoice = (typeof REVISION_STATUS_CHOICES)[number];

/** Read the status `<select>`'s value back as one of the offered choices. */
function parseRevisionStatusChoice(value: string): RevisionStatusChoice {
  return REVISION_STATUS_CHOICES.find((choice) => choice === value) ?? "";
}

export function RevisionsTab({
  modelId,
  gcodeFiles,
  allFiles,
  printerFilesByFileId,
  onModel,
  onAddRevision,
  canEdit,
}: {
  modelId: number;
  gcodeFiles: FileRead[];
  allFiles: FileRead[];
  printerFilesByFileId: Map<number, ModelPrinterFileRead[]>;
  onModel: (model: ModelRead) => void;
  onAddRevision: () => void;
  canEdit: boolean;
}) {
  useUiLocale();
  const { auth, saving, update, remove } = useRevisionUpdater(modelId, onModel);
  const { data: availableTags = [] } = useTags();

  // Revision edit form — local to this tab.
  const [editingRevisionId, setEditingRevisionId] = useState<number | null>(null);
  const [revisionLabel, setRevisionLabel] = useState("");
  const [revisionStatus, setRevisionStatus] = useState<FileRevisionStatus | "">("");
  const [revisionNotes, setRevisionNotes] = useState("");
  const [revisionRecommended, setRevisionRecommended] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<FileRead | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [selecting, setSelecting] = useState(false);
  const [selectedRevisionIds, setSelectedRevisionIds] = useState<Set<number>>(new Set());
  const [batchLabel, setBatchLabel] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const [fetchedOutcomes, setFetchedOutcomes] = useState<ArtifactOutcomeRead[]>([]);

  // Compare selection — local to this tab.
  const [compareLeftId, setCompareLeftId] = useState<number>(allFiles.at(-1)?.id ?? 0);
  const [compareRightId, setCompareRightId] = useState<number>(
    allFiles.at(-2)?.id ?? allFiles.at(-1)?.id ?? 0,
  );
  const compareLeft =
    allFiles.find((f) => f.id === compareLeftId) ?? allFiles[allFiles.length - 1] ?? null;
  const compareRight =
    allFiles.find((f) => f.id === compareRightId) ?? allFiles[allFiles.length - 2] ?? null;

  // With nothing selected on both sides there is nothing to compare, so the
  // empty outcome list is derived here instead of being cleared by the effect.
  const hasComparePair = compareLeftId !== 0 && compareRightId !== 0;
  const outcomes = hasComparePair ? fetchedOutcomes : NO_OUTCOMES;

  useEffect(() => {
    if (!hasComparePair) return;
    getArtifactOutcomes(modelId, [compareLeftId, compareRightId])
      .then(setFetchedOutcomes)
      .catch(() => setFetchedOutcomes([]));
  }, [modelId, compareLeftId, compareRightId, hasComparePair]);

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

  function deleteRevision(file: FileRead) {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setDeleteTarget(file);
  }

  async function confirmDeleteRevision() {
    if (!deleteTarget) return;
    const file = deleteTarget;
    setDeleteBusy(true);
    try {
      const ok = await remove(file);
      if (ok) {
        if (editingRevisionId === file.id) setEditingRevisionId(null);
        setDeleteTarget(null);
      }
    } finally {
      setDeleteBusy(false);
    }
  }

  async function applyBatchLabel() {
    if (selectedRevisionIds.size === 0) return;
    setBatchBusy(true);
    try {
      await batchSetRevisionLabels(Array.from(selectedRevisionIds), batchLabel);
      onModel(await getModel(modelId));
      toast.success(
        uiText("Updated {value1} revision labels", { value1: String(selectedRevisionIds.size) }),
      );
      setSelectedRevisionIds(new Set());
      setSelecting(false);
      setBatchLabel("");
    } catch (error) {
      toast.error(error);
    } finally {
      setBatchBusy(false);
    }
  }

  async function saveArtifactTags(file: FileRead, tags: string[]) {
    try {
      onModel(await replaceFileTags(modelId, file.id, tags));
      toast.success(
        uiText("Tags updated for {value1}", { value1: String(file.original_filename) }),
      );
    } catch (error) {
      toast.error(error);
      throw error;
    }
  }

  return (
    <Localized>
      <>
        <ConfirmModal
          open={!!deleteTarget}
          onClose={() => setDeleteTarget(null)}
          onConfirm={confirmDeleteRevision}
          busy={deleteBusy}
          title={uiText("Delete revision?")}
          description={
            deleteTarget
              ? uiText("Rev {value1} ({value2}) will be moved to trash.", {
                  value1: String(deleteTarget.gcode_revision_number ?? deleteTarget.version),
                  value2: String(deleteTarget.original_filename),
                })
              : uiText("This revision will be moved to trash.")
          }
        />
        <section>
          <div className="mb-4 flex items-center justify-between gap-3 border-b border-outline-variant pb-1">
            <h2 className="text-lg font-semibold text-on-surface">{uiText("G-code Revisions")}</h2>
            <div className="flex items-center gap-2">
              {gcodeFiles.length > 0 && (
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => {
                    setSelecting((value) => !value);
                    setSelectedRevisionIds(new Set());
                  }}
                  disabled={!auth.isAuthenticated}
                >
                  {selecting ? uiText("Cancel selection") : uiText("Edit labels")}
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={onAddRevision}
                disabled={!auth.isAuthenticated}
                title={auth.blockReason ?? uiText("Add G-code revision")}
              >
                <Plus className="h-3.5 w-3.5" />
                {uiText(" Add")}
              </Button>
            </div>
          </div>
          {selecting && (
            <div className="mb-3 flex flex-wrap items-center gap-2 rounded border border-outline-variant bg-surface-container-low p-2">
              <span className="font-mono text-xs text-on-surface-variant">
                {uiText("{value1} selected", { value1: String(selectedRevisionIds.size ?? "") })}
              </span>
              <Input
                value={batchLabel}
                onChange={(event) => setBatchLabel(event.target.value)}
                maxLength={128}
                placeholder={uiText("Label (blank clears)")}
                className="min-w-48 flex-1"
              />
              <Button
                type="button"
                size="xs"
                loading={batchBusy}
                disabled={selectedRevisionIds.size === 0}
                onClick={applyBatchLabel}
              >
                {uiText("Apply label")}
              </Button>
            </div>
          )}
          <div className="space-y-3">
            {gcodeFiles.length === 0 && (
              <p className="font-mono text-xs text-on-surface-variant">
                {uiText("No sliced G-code revisions yet.")}
              </p>
            )}
            {gcodeFiles.map((f) => {
              const isEditingRevision = editingRevisionId === f.id;
              const fileMeta = f.metadata;
              const uploadedTo = printerFilesByFileId.get(f.id) ?? [];
              return (
                <div
                  key={f.id}
                  className="p-3 border border-primary/30 bg-primary-fixed/15 rounded space-y-3"
                >
                  <div className="flex items-start justify-between gap-3">
                    {selecting && (
                      <Checkbox
                        checked={selectedRevisionIds.has(f.id)}
                        onChange={(checked) => {
                          setSelectedRevisionIds((current) => {
                            const next = new Set(current);
                            if (checked) next.add(f.id);
                            else next.delete(f.id);
                            return next;
                          });
                        }}
                        ariaLabel={uiText("Select revision {value1}", {
                          value1: String(f.gcode_revision_number ?? f.version),
                        })}
                      />
                    )}
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-1.5 mb-1">
                        <span className="font-mono text-2xs text-primary font-bold uppercase tracking-wider">
                          {uiText("Rev ")}
                          {f.gcode_revision_number ?? f.version}
                        </span>
                        {f.revision_label && (
                          <span className="border border-outline-variant rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
                            {f.revision_label}
                          </span>
                        )}
                        <span
                          className={`border rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider ${revisionStatusClass(f.revision_status)}`}
                        >
                          {revisionStatusLabel(f.revision_status)}
                        </span>
                        {f.is_recommended && (
                          <span className="inline-flex items-center gap-1 border border-primary/30 bg-secondary-container text-on-secondary-container rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider">
                            <Star className="h-3 w-3 fill-current" />
                            {uiText(" Recommended")}
                          </span>
                        )}
                        {uploadedTo.map((row) => (
                          <span
                            key={`${row.printer_id}-${row.remote_filename}`}
                            className="inline-flex items-center gap-1 border border-emerald-500/30 bg-emerald-500/10 text-emerald-600 rounded px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider"
                          >
                            <Wifi className="h-3 w-3" /> {row.printer_name}
                          </span>
                        ))}
                      </div>
                      <p className="text-sm text-on-surface font-medium truncate">
                        {f.original_filename}
                      </p>
                      <p className="font-mono text-2xs text-on-surface-variant">
                        {formatBytes(f.size_bytes)} · {timeAgo(f.uploaded_at)}
                        {fileMeta?.layer_height_mm
                          ? uiText(" · {value1}mm", { value1: String(fileMeta.layer_height_mm) })
                          : ""}
                        {fileMeta?.material_type ? ` · ${fileMeta.material_type}` : ""}
                        {fileMeta?.estimated_time_s
                          ? ` · ${formatDuration(fileMeta.estimated_time_s)}`
                          : ""}
                      </p>
                      <div className="mt-1.5">
                        <EntityTagsDialog
                          entityLabel={f.original_filename}
                          tags={f.tags}
                          availableTags={availableTags}
                          canEdit={canEdit}
                          help={uiText(
                            "Artifact tags make the owning Model discoverable without changing the Model’s direct tags.",
                          )}
                          onSave={(tags) => saveArtifactTags(f, tags)}
                        />
                      </div>
                      {f.revision_notes && !isEditingRevision && (
                        <p className="mt-2 text-xs text-on-surface-variant leading-relaxed">
                          {f.revision_notes}
                        </p>
                      )}
                    </div>
                    <div className="flex items-center gap-0.5 shrink-0">
                      <button
                        onClick={() => startRevisionEdit(f)}
                        disabled={!auth.isAuthenticated}
                        title={auth.blockReason ?? uiText("Edit revision")}
                        className="text-on-surface-variant hover:text-primary p-2 rounded hover:bg-surface-container-high transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <SlicerOpenButton
                        fileId={f.id}
                        // Binary G-code shares the "gcode" file_type but no slicer
                        // opens a .bgcode URL, so key off the extension to hide it.
                        fileType={
                          f.original_filename.toLowerCase().endsWith(".bgcode")
                            ? "bgcode"
                            : f.file_type
                        }
                        size="sm"
                      />
                      <button
                        type="button"
                        onClick={() =>
                          downloadAuthenticatedFile(
                            `/api/v1/files/${f.id}/download`,
                            f.original_filename,
                          ).catch((e) => toast.error(e))
                        }
                        title={uiText("Download")}
                        className="text-on-surface-variant hover:text-primary p-2 rounded hover:bg-surface-container-high transition-colors"
                      >
                        <Download className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => deleteRevision(f)}
                        disabled={!auth.isAuthenticated || saving === f.id}
                        title={auth.blockReason ?? uiText("Delete revision")}
                        className="text-on-surface-variant hover:text-error p-2 rounded hover:bg-surface-container-high transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {saving === f.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </button>
                    </div>
                  </div>

                  {isEditingRevision && (
                    <div className="space-y-2 border-t border-outline-variant pt-3">
                      <input
                        value={revisionLabel}
                        onChange={(e) => setRevisionLabel(e.target.value)}
                        maxLength={128}
                        placeholder={uiText("Revision label")}
                        className="w-full bg-surface-container-lowest border border-outline-variant rounded px-3 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
                      />
                      <select
                        value={revisionStatus}
                        onChange={(e) =>
                          setRevisionStatus(parseRevisionStatusChoice(e.target.value))
                        }
                        className="w-full bg-surface-container-lowest border border-outline-variant rounded px-3 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
                      >
                        {REVISION_STATUS_CHOICES.map((choice) => (
                          <option key={choice || "unmarked"} value={choice}>
                            {revisionStatusLabel(choice || null)}
                          </option>
                        ))}
                      </select>
                      <textarea
                        value={revisionNotes}
                        onChange={(e) => setRevisionNotes(e.target.value)}
                        rows={2}
                        placeholder={uiText(
                          "Notes about print outcome, fit, filament, or what to try next",
                        )}
                        className="w-full bg-surface-container-lowest border border-outline-variant rounded px-3 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                      />
                      <label className="flex items-center gap-2 text-xs font-mono text-on-surface-variant">
                        <input
                          type="checkbox"
                          checked={revisionRecommended}
                          onChange={(e) => setRevisionRecommended(e.target.checked)}
                          className="rounded"
                        />
                        {uiText("Mark as recommended G-code for this model")}
                      </label>
                      <div className="flex gap-2">
                        <button
                          onClick={() => setEditingRevisionId(null)}
                          disabled={saving === f.id}
                          className="flex-1 py-2 rounded border border-outline-variant text-on-surface-variant font-mono text-xs uppercase tracking-wider hover:bg-surface-container-low transition-colors disabled:opacity-50"
                        >
                          {uiText("Cancel")}
                        </button>
                        <button
                          onClick={() => saveRevision(f)}
                          disabled={saving === f.id}
                          className="flex-1 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 flex items-center justify-center gap-1.5"
                        >
                          {saving === f.id ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <Check className="h-4 w-4" />
                          )}
                          {saving === f.id ? uiText("Saving...") : uiText("Save")}
                        </button>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {allFiles.length >= 2 && compareLeft && compareRight && (
          <section>
            <h2 className="text-lg font-semibold text-on-surface mb-4 pb-1 border-b border-outline-variant flex items-center gap-2">
              <GitCompare className="h-4 w-4" />
              {uiText(" Compare Artifacts")}
            </h2>
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2">
                <select
                  value={compareLeft?.id ?? ""}
                  onChange={(e) => setCompareLeftId(Number(e.target.value))}
                  className="bg-surface border border-outline-variant rounded px-2 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {allFiles.map((f) => (
                    <option key={f.id} value={f.id}>
                      {uiText("{value1} v{value2} — {value3}", {
                        value1: String(f.file_type.toUpperCase() ?? ""),
                        value2: String(f.version ?? ""),
                        value3: String(f.original_filename ?? ""),
                      })}
                    </option>
                  ))}
                </select>
                <select
                  value={compareRight?.id ?? ""}
                  onChange={(e) => setCompareRightId(Number(e.target.value))}
                  className="bg-surface border border-outline-variant rounded px-2 py-2 font-mono text-xs text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
                >
                  {allFiles.map((f) => (
                    <option key={f.id} value={f.id}>
                      {uiText("{value1} v{value2} — {value3}", {
                        value1: String(f.file_type.toUpperCase() ?? ""),
                        value2: String(f.version ?? ""),
                        value3: String(f.original_filename ?? ""),
                      })}
                    </option>
                  ))}
                </select>
              </div>
              <RevisionCompare left={compareLeft} right={compareRight} outcomes={outcomes} />
            </div>
          </section>
        )}
      </>
    </Localized>
  );
}
