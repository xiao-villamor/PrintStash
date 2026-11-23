"use client";

import { RevisionDeliveryActions } from "./revision-delivery-actions";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { Box, MoreHorizontal, Trash2, Star, Wifi } from "lucide-react";

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
import { ChoiceField } from "./choice-field";
import { Modal } from "@/components/ui/modal";
import { RevisionCompare, type ComparisonMode } from "./revision-compare";
import type { GcodePlates } from "@/lib/api/plates";
import { PlateEstimates } from "./plate-estimates";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { HashValue } from "./file-hashes";
import { Localized } from "@/components/ui/localized";
import { useRevisionUpdater } from "./use-revision-updater";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { isTypingTarget, keyboardOverlayOpen } from "@/lib/keyboard-commands";

const NO_OUTCOMES: ArtifactOutcomeRead[] = [];

/** The revision statuses the edit form offers, in menu order; `""` is unmarked. */
const REVISION_STATUS_CHOICES = ["", "known_good", "needs_test", "failed", "archived"] as const;

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
  plateEstimates,
  selectedPlate = null,
  onPreview,
  onInspect,
  startComparing = false,
  backLabel = "Back to revisions",
  onReturnToOrigin,
  comparisonMode = "gcode",
  printerless = false,
  onSend,
  onSettings,
  onLogPrint,
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
  plateEstimates?: GcodePlates | null;
  selectedPlate?: number | null;
  onPreview?: (fileId: number) => void;
  onInspect?: (id: number) => void;
  startComparing?: boolean;
  backLabel?: "Back to revisions" | "Back to overview" | "Back to files" | "Back to print settings";
  onReturnToOrigin?: () => void;
  comparisonMode?: ComparisonMode;
  printerless?: boolean;
  onSend?: (mode: "send" | "queue") => void;
  onSettings?: () => void;
  onLogPrint?: (fileId: number, state: "completed" | "failed") => void;
  jobs?: ModelPrintJobRead[] | null;
}) {
  useUiLocale();
  const { auth, saving, update, remove } = useRevisionUpdater(modelId, onModel);
  const { data: availableTags = [] } = useTags();
  const [localInspectedId, setLocalInspectedId] = useState(gcodeFiles[0]?.id ?? null);
  const selectedId = inspectedFileId ?? localInspectedId;
  const [comparing, setComparing] = useState(startComparing);
  const [actionsFileId, setActionsFileId] = useState<number | null>(null);
  const compareTrigger = useRef<HTMLButtonElement>(null);
  const returningFromCompare = useRef(false);
  function closeCompare() {
    if (onReturnToOrigin) {
      onReturnToOrigin();
      return;
    }
    returningFromCompare.current = true;
    setComparing(false);
  }
  useEffect(() => {
    if (!comparing && returningFromCompare.current) {
      compareTrigger.current?.focus();
      returningFromCompare.current = false;
    }
  }, [comparing]);
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
  const scopedCount =
    comparisonMode === "source" ? allFiles.length - gcodeFiles.length : gcodeFiles.length;
  const initialMode = scopedCount < 2 && allFiles.length >= 2 ? "all" : comparisonMode;
  const [scope, setScope] = useState({ intent: comparisonMode, mode: initialMode });
  if (scope.intent !== comparisonMode) setScope({ intent: comparisonMode, mode: initialMode });
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
        <section
          hidden={comparing}
          onKeyDown={(event) => {
            if (
              event.defaultPrevented ||
              event.nativeEvent.isComposing ||
              (event.repeat && event.key !== "ArrowUp" && event.key !== "ArrowDown") ||
              event.ctrlKey ||
              event.metaKey ||
              event.altKey ||
              (event.shiftKey && event.key !== "1" && event.key !== "2") ||
              isTypingTarget(event.target) ||
              keyboardOverlayOpen()
            )
              return;
            const focused =
              event.target instanceof HTMLElement
                ? event.target.closest<HTMLElement>("[data-revision-id]")
                : null;
            if (!focused) return;
            const index = gcodeFiles.findIndex(
              (file) => file.id === Number(focused.dataset.revisionId),
            );
            if (index < 0) return;
            if (event.key === "ArrowUp" || event.key === "ArrowDown") {
              event.preventDefault();
              const next =
                gcodeFiles[
                  Math.max(
                    0,
                    Math.min(gcodeFiles.length - 1, index + (event.key === "ArrowDown" ? 1 : -1)),
                  )
                ];
              inspect(next.id);
              event.currentTarget
                .querySelector<HTMLButtonElement>(`button[data-revision-id="${next.id}"]`)
                ?.focus();
            } else if ((event.key === "1" || event.key === "2") && onLogPrint) {
              event.preventDefault();
              inspect(gcodeFiles[index].id);
              onLogPrint(gcodeFiles[index].id, event.key === "1" ? "completed" : "failed");
            }
          }}
        >
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-base font-semibold text-on-surface">
              {uiText("G-code revisions")}{" "}
              <span className="ml-1 text-xs font-normal text-muted-foreground">
                {gcodeFiles.length}
              </span>
            </h2>
            <div className="flex items-center gap-2">
              {allFiles.length >= 2 && (
                <Button
                  ref={compareTrigger}
                  variant="ghost"
                  size="sm"
                  onClick={() => {
                    setScope({ intent: comparisonMode, mode: initialMode });
                    setComparing(true);
                  }}
                >
                  {uiText("Compare")}
                </Button>
              )}
              {gcodeFiles.length > 1 && (
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
                {uiText("Add revision")}
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
          {!comparing && (gcodeFiles.length > 1 || selecting) && (
            <div className="overflow-x-auto">
              <table className="detail-revisions-table">
                <thead>
                  <tr>
                    <th>{uiText("Revision")}</th>
                    <th>{uiText("Printer results")}</th>
                    <th>{uiText("Material")}</th>
                    <th>{uiText("Layer")}</th>
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
                        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
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
                            data-revision-id={file.id}
                            className="min-w-0 wrap-anywhere text-left"
                            aria-pressed={file.id === selectedId}
                            onClick={() => inspect(file.id)}
                          >
                            <strong className="whitespace-nowrap">
                              {uiText("Rev {value1}", {
                                value1: String(file.gcode_revision_number ?? file.version),
                              })}
                            </strong>
                            {file.revision_label && (
                              <span className="ms-2 font-normal">{` ${file.revision_label}`}</span>
                            )}
                          </button>
                          {file.is_recommended && (
                            <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-normal text-muted-foreground">
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
                      <td>
                        {file.id === selectedId &&
                        plateEstimates?.plate_metadata.some(
                          (entry) => entry.metadata.estimated_time_s != null,
                        ) &&
                        plateEstimates.plates.length > 1
                          ? uiText("Per plate")
                          : formatDuration(file.metadata?.estimated_time_s ?? null)}
                      </td>
                      <td>
                        {file.id === selectedId &&
                        plateEstimates?.plate_metadata.some(
                          (entry) => entry.metadata.filament_weight_g != null,
                        ) &&
                        plateEstimates.plates.length > 1
                          ? uiText("Per plate")
                          : formatGrams(file.metadata?.filament_weight_g)}
                      </td>
                    </tr>
                    <tr>
                      <td colSpan={6} className="detail-revision-table-note">
                        {file.revision_notes}
                        {file.revision_status && (
                          <span className={file.revision_notes ? "ml-2" : undefined}>
                            {revisionStatusLabel(file.revision_status)}
                          </span>
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
                    <div key={f.id} className="detail-inspected-revision space-y-4">
                      <div className="space-y-2">
                        <div className="flex flex-wrap items-center justify-between gap-3">
                          <h3
                            data-revision-id={f.id}
                            tabIndex={gcodeFiles.length === 1 ? 0 : undefined}
                            className="flex min-w-0 flex-wrap items-baseline gap-2 text-xl font-semibold"
                          >
                            <span className="whitespace-nowrap">
                              {uiText("Rev {value1}", {
                                value1: String(f.gcode_revision_number ?? f.version),
                              })}
                            </span>
                            {f.revision_status && gcodeFiles.length === 1 && (
                              <span className="rounded border border-border px-2 py-0.5 text-xs font-medium">
                                {revisionStatusLabel(f.revision_status)}
                              </span>
                            )}
                            {f.is_recommended && (
                              <span className="inline-flex items-center gap-1 whitespace-nowrap text-xs font-medium text-muted-foreground">
                                <Star className="h-3.5 w-3.5" />
                                {uiText("Recommended")}
                              </span>
                            )}
                            {f.revision_label && (
                              <span className="min-w-0 wrap-anywhere font-normal">
                                {f.revision_label}
                              </span>
                            )}
                          </h3>
                          {onPreview && (
                            <Button variant="outline" onClick={() => onPreview(f.id)}>
                              <Box className="h-4 w-4" />
                              {uiText("Preview G-code")}
                            </Button>
                          )}
                        </div>
                        <p className="wrap-anywhere text-sm text-muted-foreground">
                          {f.original_filename}
                        </p>
                        <div className="flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                          <span>
                            {uiText("Added")} {timeAgo(f.uploaded_at)}
                          </span>
                          <span>{formatBytes(f.size_bytes)}</span>
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
                      <section
                        className="border-t border-border pt-3"
                        aria-label={uiText("Print setup")}
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <h4 className="text-sm font-semibold">{uiText("Print setup")}</h4>
                          {onSettings && (
                            <Button size="xs" variant="ghost" onClick={onSettings}>
                              {uiText("Full print settings")}
                            </Button>
                          )}
                        </div>
                        <dl className="mt-2 grid grid-cols-2 gap-3 @lg:grid-cols-4">
                          {[
                            [uiText("Printer profile"), fileMeta?.printer_model],
                            [uiText("Material"), fileMeta?.material_type],
                            [uiText("Layer height"), formatMillimeters(fileMeta?.layer_height_mm)],
                            [uiText("Nozzle"), formatMillimeters(fileMeta?.nozzle_diameter_mm)],
                          ].map(([label, value]) => (
                            <div key={label}>
                              <dt className="text-xs text-muted-foreground">{label}</dt>
                              <dd className="wrap-anywhere text-sm font-medium">
                                {value ?? uiText("Not recorded")}
                              </dd>
                            </div>
                          ))}
                        </dl>
                      </section>
                      {plateEstimates && plateEstimates.plates.length > 1 ? (
                        <PlateEstimates data={plateEstimates} selected={selectedPlate} />
                      ) : (
                        <dl className="flex flex-wrap gap-x-6 text-sm">
                          <div>
                            <dt className="text-xs text-muted-foreground">
                              {uiText("Estimated time")}
                            </dt>
                            <dd>{formatDuration(fileMeta?.estimated_time_s ?? null)}</dd>
                          </div>
                          <div>
                            <dt className="text-xs text-muted-foreground">
                              {uiText("Filament weight")}
                            </dt>
                            <dd>{formatGrams(fileMeta?.filament_weight_g)}</dd>
                          </div>
                        </dl>
                      )}
                      {gcodeFiles.length === 1 && (
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
                          <RevisionResults fileId={f.id} jobs={jobs} />
                          {f.revision_notes && (
                            <p className="w-full wrap-anywhere">{f.revision_notes}</p>
                          )}
                        </div>
                      )}
                      <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
                        {onSend && (
                          <RevisionDeliveryActions printerless={printerless} onSend={onSend} />
                        )}
                        <div className="ml-auto flex flex-wrap items-center gap-2">
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
                            showLabel
                          />
                        </div>
                      </div>
                      <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={uiText("Edit revision")}
                          disabled={!auth.isAuthenticated || !canEdit}
                          onClick={() => startRevisionEdit(f)}
                        >
                          {uiText("Edit revision")}
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
                        {!f.is_recommended && canEdit && (
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
                        )}
                        <DropdownMenu
                          open={actionsFileId === f.id}
                          onOpenChange={(open) => setActionsFileId(open ? f.id : null)}
                          portal
                          className="ml-auto"
                          trigger={
                            <Button
                              variant="ghost"
                              size="xs"
                              data-menu-trigger
                              aria-haspopup="menu"
                              aria-expanded={actionsFileId === f.id}
                              onClick={() => setActionsFileId(actionsFileId === f.id ? null : f.id)}
                            >
                              <MoreHorizontal className="h-4 w-4" />
                              {uiText("nav.more")}
                            </Button>
                          }
                          contentClassName="w-48 rounded-md border border-border bg-popover p-1 shadow-lg"
                        >
                          <Button
                            role="menuitem"
                            size="sm"
                            variant="ghost"
                            className="w-full justify-start text-destructive"
                            disabled={!auth.isAuthenticated || !canEdit || saving === f.id}
                            onClick={() => {
                              setActionsFileId(null);
                              deleteRevision(f);
                            }}
                          >
                            <Trash2 className="h-4 w-4" />
                            {uiText("Move to trash")}
                          </Button>
                        </DropdownMenu>
                      </div>

                      {isEditingRevision && (
                        <Modal
                          open
                          onClose={() => {
                            if (saving !== f.id) setEditingRevisionId(null);
                          }}
                          title={uiText("Edit revision")}
                          className="max-w-[640px]"
                          description={uiText(
                            "Set a manual status for this Revision. It does not change the recommended Revision or its measured history.",
                          )}
                          footer={
                            <>
                              <Button
                                variant="outline"
                                onClick={() => setEditingRevisionId(null)}
                                disabled={saving === f.id}
                              >
                                {uiText("Cancel")}
                              </Button>
                              <Button onClick={() => saveRevision(f)} loading={saving === f.id}>
                                {uiText("Save Revision")}
                              </Button>
                            </>
                          }
                        >
                          <div className="space-y-5">
                            <label className="grid gap-2 text-sm">
                              {uiText("Revision label")}
                              <input
                                value={revisionLabel}
                                onChange={(e) => setRevisionLabel(e.target.value)}
                                maxLength={128}
                                placeholder={uiText("Revision label")}
                                className="w-full bg-card border border-input rounded px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary"
                              />
                            </label>
                            <ChoiceField<FileRevisionStatus | "">
                              label={uiText("Manual label")}
                              value={revisionStatus}
                              options={REVISION_STATUS_CHOICES.map((value) => ({
                                value,
                                label: revisionStatusLabel(value || null),
                              }))}
                              onChange={setRevisionStatus}
                              inline
                            />
                            <label className="grid gap-2 text-sm">
                              {uiText("Notes")}
                              <textarea
                                value={revisionNotes}
                                onChange={(e) => setRevisionNotes(e.target.value)}
                                rows={2}
                                placeholder={uiText(
                                  "Notes about print outcome, fit, filament, or what to try next",
                                )}
                                className="w-full bg-card border border-input rounded px-3 py-2 text-sm text-on-surface focus:outline-none focus:ring-2 focus:ring-primary resize-none"
                              />
                            </label>
                            <ChoiceField
                              label={uiText("Recommended Revision")}
                              value={revisionRecommended ? "recommend" : "keep"}
                              options={[
                                { value: "keep", label: uiText("Keep current recommendation") },
                                {
                                  value: "recommend",
                                  label: uiText("Make this Revision recommended"),
                                },
                              ]}
                              onChange={(value) => setRevisionRecommended(value === "recommend")}
                              inline
                            />
                            <p className="border-t border-border pt-4 text-xs text-muted-foreground">
                              {uiText(
                                "Saving Failed or Archived never removes the recommended marker.",
                              )}
                            </p>
                          </div>
                        </Modal>
                      )}
                    </div>
                  );
                })}
          </div>
        </section>

        {comparing && !fallback && (
          <div className="space-y-3">
            <p>{uiText("Add a second file to compare recorded information.")}</p>
            <Button variant="ghost" onClick={closeCompare}>
              {uiText(backLabel)}
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
            backLabel={backLabel}
            onClose={closeCompare}
            mode={mode}
            onModeChange={(next) => setScope({ intent: comparisonMode, mode: next })}
          />
        )}
      </>
    </Localized>
  );
}
