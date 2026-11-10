"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useState, useSyncExternalStore } from "react";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { Check, Loader2, Plus, Star, Wifi } from "lucide-react";

import {
  batchSetRevisionLabels,
  downloadAuthenticatedFile,
  getArtifactOutcomes,
  getModel,
  replaceFileTags,
} from "@/lib/api";
import { useTags } from "@/lib/queries";
import { formatBytes, formatDuration, formatGrams, formatMillimeters, timeAgo } from "@/lib/format";
import { toast } from "@/lib/toast";
import {
  FileRead,
  ArtifactOutcomeRead,
  FileRevisionStatus,
  ModelPrinterFileRead,
  ModelRead,
  ModelPrintJobRead,
} from "@/types";

import { PRINT_JOB_PRESENTATION, revisionStatusLabel } from "./presentation";
import { RevisionCompare, type ComparisonMode } from "./revision-compare";
import { HashValue } from "./file-hashes";
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

function RevisionResults({ fileId, jobs }: { fileId: number; jobs: ModelPrintJobRead[] | null }) {
  if (jobs === null) return <span className="text-muted-foreground">{uiText("Not recorded")}</span>;
  const results = new Map<
    string,
    { printer: string; state: ModelPrintJobRead["state"]; count: number }
  >();
  for (const job of jobs) {
    if (job.file_id !== fileId) continue;
    const key = JSON.stringify([job.printer_name, job.state]);
    const row = results.get(key);
    if (row) row.count++;
    else results.set(key, { printer: job.printer_name, state: job.state, count: 1 });
  }
  return results.size ? (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {[...results.entries()].map(([key, result]) => (
        <span key={key} className={result.state === "failed" ? "text-destructive" : undefined}>
          {result.printer} · {result.count}{" "}
          {result.state === "completed"
            ? uiText("Successful")
            : PRINT_JOB_PRESENTATION[result.state].label}
        </span>
      ))}
    </div>
  ) : (
    <span className="text-muted-foreground">{uiText("No recorded prints")}</span>
  );
}

export function RevisionsTab({
  modelId,
  gcodeFiles,
  allFiles,
  printerFilesByFileId,
  onModel,
  onAddRevision,
  canEdit,
  inspectedFileId,
  onInspect,
  startComparing = false,
  comparisonMode = "gcode",
  onSend,
  onSettings,
  jobs = null,
}: {
  modelId: number;
  gcodeFiles: FileRead[];
  allFiles: FileRead[];
  printerFilesByFileId: Map<number, ModelPrinterFileRead[]>;
  onModel: (model: ModelRead) => void;
  onAddRevision: () => void;
  canEdit: boolean;
  inspectedFileId?: number;
  onInspect?: (id: number) => void;
  startComparing?: boolean;
  comparisonMode?: ComparisonMode;
  onSend?: (mode: "send" | "queue") => void;
  onSettings?: () => void;
  jobs?: ModelPrintJobRead[] | null;
}) {
  useUiLocale();
  const { auth, saving, update, remove } = useRevisionUpdater(modelId, onModel);
  const { data: availableTags = [] } = useTags();
  const [localInspectedId, setLocalInspectedId] = useState(gcodeFiles[0]?.id ?? null);
  const selectedId = inspectedFileId ?? localInspectedId;
  const [comparing, setComparing] = useState(startComparing);
  function inspect(id: number) {
    setLocalInspectedId(id);
    onInspect?.(id);
  }

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
  const [fetchedOutcomes, setFetchedOutcomes] = useState<{
    key: string;
    rows: ArtifactOutcomeRead[];
  } | null>(null);

  // This owner controls both visible mode and the exact pair used for outcome reads.
  const [scope, setScope] = useState({ intent: comparisonMode, mode: comparisonMode });
  if (scope.intent !== comparisonMode) setScope({ intent: comparisonMode, mode: comparisonMode });
  const mode = scope.intent === comparisonMode ? scope.mode : comparisonMode;
  const candidates = allFiles.filter(
    (file) => mode === "all" || (file.file_type === "gcode") === (mode === "gcode"),
  );
  const [compareLeftId, setCompareLeftId] = useState<number | null>(
    comparisonMode === "source"
      ? (candidates[0]?.id ?? null)
      : (inspectedFileId ?? allFiles.at(-1)?.id ?? null),
  );
  const [compareRightId, setCompareRightId] = useState<number | null>(
    comparisonMode === "source"
      ? (candidates[1]?.id ?? null)
      : (allFiles.findLast((file) => file.id !== (inspectedFileId ?? allFiles.at(-1)?.id))?.id ??
          null),
  );
  const compareLeft =
    candidates.find((file) => file.id === compareLeftId) ?? candidates.at(-1) ?? null;
  const compareRight =
    candidates.find((file) => file.id === compareRightId && file.id !== compareLeft?.id) ??
    candidates.findLast((file) => file.id !== compareLeft?.id) ??
    null;
  const fallback = allFiles[0] ?? null;
  const leftId = compareLeft?.id ?? null;
  const rightId = compareRight?.id ?? null;
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const outcomeKey =
    auth.isAuthenticated && session === currentSession && leftId !== null && rightId !== null
      ? `${session}:${modelId}:${leftId}:${rightId}`
      : null;
  const outcomes =
    outcomeKey !== null && fetchedOutcomes?.key === outcomeKey ? fetchedOutcomes.rows : NO_OUTCOMES;

  useEffect(() => {
    if (outcomeKey === null || leftId === null || rightId === null) return;
    let retired = false;
    const publish = (rows: ArtifactOutcomeRead[]) => {
      if (!retired && session === getSessionVersion())
        setFetchedOutcomes({ key: outcomeKey, rows });
    };
    getArtifactOutcomes(modelId, [leftId, rightId])
      .then(publish)
      .catch(() => publish([]));
    return () => {
      retired = true;
    };
  }, [modelId, leftId, rightId, outcomeKey, session]);

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
        <section hidden={comparing}>
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-on-surface">
              {uiText("G-code Revisions")}
            </h2>
            <div className="flex items-center gap-2">
              {allFiles.length >= 2 && (
                <Button variant="ghost" size="sm" onClick={() => setComparing((value) => !value)}>
                  {comparing ? uiText("Back to revisions") : uiText("Compare")}
                </Button>
              )}
              {gcodeFiles.length > 0 && (
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => {
                    setSelecting((value) => !value);
                    setSelectedRevisionIds(new Set());
                  }}
                  disabled={!auth.isAuthenticated || !canEdit}
                >
                  {selecting ? uiText("Cancel selection") : uiText("Edit labels")}
                </Button>
              )}
              <Button
                type="button"
                variant="outline"
                size="xs"
                onClick={onAddRevision}
                disabled={!auth.isAuthenticated || !canEdit}
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
          {!comparing && gcodeFiles.length > 0 && (
            <div className="overflow-x-auto">
              <table className="detail-revisions-table">
                <thead>
                  <tr>
                    <th>{uiText("Revision")}</th>
                    <th>{uiText("Printer results")}</th>
                    <th>{uiText("Material")}</th>
                    <th>{uiText("Layer height")}</th>
                    <th>{uiText("Time")}</th>
                    <th>{uiText("Filament")}</th>
                  </tr>
                </thead>
                {gcodeFiles.map((file) => (
                  <tbody
                    key={file.id}
                    className={file.id === selectedId ? "is-inspected" : undefined}
                  >
                    <tr>
                      <th scope="row">
                        <div className="flex items-center gap-2">
                          {selecting && (
                            <Checkbox
                              checked={selectedRevisionIds.has(file.id)}
                              onChange={(checked) =>
                                setSelectedRevisionIds((current) => {
                                  const next = new Set(current);
                                  if (checked) next.add(file.id);
                                  else next.delete(file.id);
                                  return next;
                                })
                              }
                              ariaLabel={uiText("Select revision {value1}", {
                                value1: String(file.gcode_revision_number ?? file.version),
                              })}
                            />
                          )}
                          <button
                            type="button"
                            className="text-left"
                            aria-pressed={file.id === selectedId}
                            onClick={() => inspect(file.id)}
                          >
                            <strong>
                              {uiText("Rev {value1}", {
                                value1: String(file.gcode_revision_number ?? file.version),
                              })}
                            </strong>
                            {file.revision_label && (
                              <span className="ml-2 font-normal">{file.revision_label}</span>
                            )}
                          </button>
                          {file.is_recommended && (
                            <span className="inline-flex items-center gap-1 text-xs font-normal text-muted-foreground">
                              <Star className="h-3 w-3" />
                              {uiText("Recommended")}
                            </span>
                          )}
                        </div>
                      </th>
                      <td>
                        <RevisionResults fileId={file.id} jobs={jobs} />
                      </td>
                      <td>{file.metadata?.material_type ?? uiText("Not recorded")}</td>
                      <td>{formatMillimeters(file.metadata?.layer_height_mm)}</td>
                      <td>{formatDuration(file.metadata?.estimated_time_s ?? null)}</td>
                      <td>{formatGrams(file.metadata?.filament_weight_g)}</td>
                    </tr>
                    <tr>
                      <td colSpan={6} className="detail-revision-table-note">
                        {file.revision_notes ?? file.original_filename}
                        {file.revision_status && (
                          <span className="ml-2">{revisionStatusLabel(file.revision_status)}</span>
                        )}
                      </td>
                    </tr>
                  </tbody>
                ))}
              </table>
            </div>
          )}
          <div className="space-y-3">
            {gcodeFiles.length === 0 && (
              <p className="font-mono text-xs text-on-surface-variant">
                {uiText("No sliced G-code revisions yet.")}
              </p>
            )}
            {!comparing &&
              !selecting &&
              gcodeFiles
                .filter((file) => file.id === selectedId)
                .map((f) => {
                  const isEditingRevision = editingRevisionId === f.id;
                  const fileMeta = f.metadata;
                  const uploadedTo = printerFilesByFileId.get(f.id) ?? [];
                  return (
                    <div key={f.id} className="detail-inspected-revision space-y-3 pt-5">
                      <div className="space-y-2">
                        <h3 className="flex flex-wrap items-baseline gap-2 text-sm font-semibold">
                          {uiText("Rev {value1}", {
                            value1: String(f.gcode_revision_number ?? f.version),
                          })}
                          {f.revision_label && (
                            <span className="font-normal">{f.revision_label}</span>
                          )}
                        </h3>
                        <p className="break-all text-sm">{f.original_filename}</p>
                        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                          <span>
                            {uiText("Added")} {timeAgo(f.uploaded_at)}
                          </span>
                          <span>{formatBytes(f.size_bytes)}</span>
                          <span>
                            {formatMillimeters(fileMeta?.nozzle_diameter_mm)} {uiText("Nozzle")}
                          </span>
                          <HashValue
                            sha={f.sha256}
                            label={uiText("File SHA-256")}
                            filename={f.original_filename}
                          />
                        </div>
                        {uploadedTo.length > 0 && (
                          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
                            {uploadedTo.map((row) => (
                              <span
                                key={`${row.printer_id}-${row.remote_filename}`}
                                className="inline-flex items-center gap-1"
                              >
                                <Wifi className="h-3 w-3" />
                                {row.printer_name}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {onSend && (
                          <>
                            <Button onClick={() => onSend("send")}>
                              {uiText("Send to printer")}
                            </Button>
                            <Button variant="outline" onClick={() => onSend("queue")}>
                              {uiText("Add to queue")}
                            </Button>
                          </>
                        )}
                        <Button
                          variant="outline"
                          onClick={() =>
                            void downloadAuthenticatedFile(
                              `/api/v1/files/${f.id}/download`,
                              f.original_filename,
                            ).catch((error) => toast.error(error))
                          }
                        >
                          {uiText("Download")}
                        </Button>
                        <SlicerOpenButton
                          fileId={f.id}
                          fileType={
                            f.original_filename.toLowerCase().endsWith(".bgcode")
                              ? "bgcode"
                              : f.file_type
                          }
                          size="sm"
                        />
                        {onSettings && (
                          <Button variant="ghost" onClick={onSettings}>
                            {uiText("Print settings")}
                          </Button>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={uiText("Edit revision")}
                          disabled={!auth.isAuthenticated || !canEdit}
                          onClick={() => startRevisionEdit(f)}
                        >
                          {uiText("Edit")}
                        </Button>
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
                        {f.is_recommended ? (
                          <span className="text-xs text-muted-foreground">
                            {uiText("Recommended revision")}
                          </span>
                        ) : (
                          canEdit && (
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={saving === f.id}
                              onClick={() =>
                                void update(f, {
                                  is_recommended: true,
                                  revision_status: "known_good",
                                })
                              }
                            >
                              {uiText("Recommend")}
                            </Button>
                          )
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          className="ml-auto text-destructive"
                          aria-label={uiText("Delete revision")}
                          disabled={!auth.isAuthenticated || !canEdit || saving === f.id}
                          onClick={() => deleteRevision(f)}
                        >
                          {uiText("Move to trash")}
                        </Button>
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

        {comparing && !fallback && (
          <div className="space-y-3">
            <p>{uiText("Add a second file to compare recorded information.")}</p>
            <Button variant="ghost" onClick={() => setComparing(false)}>
              {uiText("Back to revisions")}
            </Button>
          </div>
        )}
        {comparing && fallback && (
          <RevisionCompare
            left={compareLeft ?? fallback}
            right={compareRight ?? compareLeft ?? fallback}
            outcomes={outcomes}
            files={allFiles}
            onSelectLeft={setCompareLeftId}
            onSelectRight={setCompareRightId}
            onClose={() => setComparing(false)}
            mode={mode}
            onModeChange={(next) => setScope({ intent: comparisonMode, mode: next })}
          />
        )}
      </>
    </Localized>
  );
}
