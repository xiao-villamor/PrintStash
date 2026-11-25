"use client";

import { RevisionStatusBadge } from "./model-print-summary";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useRef, useState, useSyncExternalStore, type ReactNode } from "react";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { ChevronRight, MoreVertical, Trash2, Wifi } from "lucide-react";

import {
  batchSetRevisionLabels,
  downloadAuthenticatedFile,
  getArtifactOutcomes,
  getModel,
  replaceFileTags,
} from "@/lib/api";
import { useTags } from "@/lib/queries";
import { formatBytes, timeAgo } from "@/lib/format";
import { useGcodePlates } from "@/lib/queries/file-plates";
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
import { formatPlateEstimate } from "./format-plate-estimate";
import { InspectorSection, PropertyList, PropertyRow, SoftCard } from "./inspector-section";
import { summarizePrintEstimate } from "./print-estimate";
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

function isArchive3mf(file: FileRead) {
  return file.original_filename.toLowerCase().endsWith(".3mf");
}

function MenuItem({
  children,
  onSelect,
  disabled,
  destructive = false,
}: {
  children: ReactNode;
  onSelect: () => void;
  disabled?: boolean;
  destructive?: boolean;
}) {
  return (
    <Button
      role="menuitem"
      size="sm"
      variant="ghost"
      className={`w-full justify-start ${destructive ? "text-destructive" : ""}`}
      disabled={disabled}
      onClick={onSelect}
    >
      {children}
    </Button>
  );
}

/** One revision in the list: identity, status and the facts that choose between revisions. */
function RevisionListRow({
  modelId,
  file,
  jobs,
  selected,
  onInspect,
}: {
  modelId: number;
  file: FileRead;
  jobs: ModelPrintJobRead[] | null;
  selected: boolean;
  onInspect: () => void;
}) {
  const estimates = useGcodePlates(modelId, isArchive3mf(file) ? file.id : null);
  const summary = summarizePrintEstimate(file, estimates);
  const time = summary.kind === "single" || summary.kind === "plates" ? summary.time : null;
  const weight = summary.kind === "single" || summary.kind === "plates" ? summary.weight : null;
  const prints = jobs?.filter((job) => job.file_id === file.id).length ?? null;
  const meta = [
    file.metadata?.material_type ?? null,
    time != null ? formatPlateEstimate("estimated_time_s", time) : null,
    weight != null ? formatPlateEstimate("filament_weight_g", weight) : null,
    prints === null
      ? null
      : prints === 0
        ? uiText("No prints")
        : uiText("{count} prints", { count: prints }),
    timeAgo(file.uploaded_at),
  ].filter((part) => part !== null);
  return (
    <button
      type="button"
      data-revision-id={file.id}
      aria-pressed={selected}
      onClick={onInspect}
      className={`flex min-w-0 flex-1 flex-col gap-1 border-s-2 px-4 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${selected ? "border-foreground bg-accent/60" : "border-transparent hover:bg-muted/50"}`}
    >
      <span className="flex min-w-0 flex-wrap items-center gap-2 text-sm">
        <strong className="whitespace-nowrap font-semibold">
          {uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) })}
        </strong>
        {file.revision_label && (
          <span className="min-w-0 wrap-anywhere text-muted-foreground" title={file.revision_label}>
            {file.revision_label}
          </span>
        )}
        <RevisionStatusBadge status={file.revision_status} />
        {file.is_recommended && (
          <span className="text-muted-foreground">{uiText("Recommended")}</span>
        )}
      </span>
      <span className="text-xs tabular-nums text-muted-foreground">{meta.join(" · ")}</span>
    </button>
  );
}

/** The inspected revision's estimate, with the origin the costing view repeats. */
function EstimateRow({ modelId, file }: { modelId: number; file: FileRead }) {
  const estimates = useGcodePlates(modelId, isArchive3mf(file) ? file.id : null);
  const summary = summarizePrintEstimate(file, estimates);
  const time = summary.kind === "single" || summary.kind === "plates" ? summary.time : null;
  const weight = summary.kind === "single" || summary.kind === "plates" ? summary.weight : null;
  const text = [
    time != null ? formatPlateEstimate("estimated_time_s", time) : null,
    weight != null ? formatPlateEstimate("filament_weight_g", weight) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  const origin =
    summary.kind === "single"
      ? uiText("G-code estimate")
      : summary.kind === "plates"
        ? uiText("G-code estimate · sum of {count} plates", { count: summary.count })
        : summary.kind === "partial-plates"
          ? uiText("modelDetail.plateEstimatesIncomplete")
          : null;
  return (
    <PropertyRow label={uiText("modelDetail.estimate")}>
      <div className="min-w-0">
        {text ? (
          <span className="tabular-nums">{text}</span>
        ) : (
          <span className="text-muted-foreground">{uiText("Not in the file")}</span>
        )}
        {origin && <span className="block text-muted-foreground">{origin}</span>}
      </div>
    </PropertyRow>
  );
}

/** A footer row that opens Print settings, naming plates when the revision has several. */
function SettingsLink({
  modelId,
  file,
  onSettings,
}: {
  modelId: number;
  file: FileRead;
  onSettings: () => void;
}) {
  const estimates = useGcodePlates(modelId, isArchive3mf(file) ? file.id : null);
  const multi = (estimates?.plates.length ?? 0) > 1;
  return (
    <button
      type="button"
      onClick={onSettings}
      className="flex w-full items-center justify-between gap-3 border-t border-border bg-muted/40 px-4 py-2.5 text-start text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
    >
      {multi ? uiText("Plate estimates and print setup") : uiText("Print setup")}
      <ChevronRight className="size-4 text-muted-foreground" aria-hidden />
    </button>
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
  onPreview,
  onInspect,
  startComparing = false,
  backLabel = "Back to revisions",
  onReturnToOrigin,
  comparisonMode = "gcode",
  onSettings,
  onFiles,
  onActivity,
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
  onPreview?: (fileId: number) => void;
  onInspect?: (id: number) => void;
  startComparing?: boolean;
  backLabel?: "Back to revisions" | "Back to files" | "Back to print settings";
  onReturnToOrigin?: () => void;
  comparisonMode?: ComparisonMode;
  onSettings?: () => void;
  onFiles?: () => void;
  onActivity?: (fileId: number) => void;
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
  const [tagsDialog, setTagsDialog] = useState<{ fileId: number | null; opened: number }>({
    fileId: null,
    opened: 0,
  });
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
          <InspectorSection
            title={
              <>
                {uiText("G-code revisions")} · {gcodeFiles.length}
              </>
            }
            actions={
              <div className="flex items-center gap-1">
                {allFiles.length >= 2 && (
                  <Button
                    ref={compareTrigger}
                    variant="ghost"
                    size="xs"
                    onClick={() => {
                      setScope({ intent: comparisonMode, mode: initialMode });
                      setComparing(true);
                    }}
                  >
                    {gcodeFiles.length >= 2 ? uiText("Compare") : uiText("Compare files")}
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
            }
          >
            {selecting && (
              <div className="mb-3 flex flex-wrap items-center gap-2 border border-border bg-muted/40 p-2">
                <span className="text-xs text-muted-foreground">
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
                <Button
                  type="button"
                  variant="outline"
                  size="xs"
                  onClick={() => {
                    setSelecting(false);
                    setSelectedRevisionIds(new Set());
                  }}
                >
                  {uiText("Cancel selection")}
                </Button>
              </div>
            )}
            {!comparing && gcodeFiles.length > 0 && (
              <SoftCard>
                <ul aria-label={uiText("Revisions")} className="divide-y divide-border">
                  {gcodeFiles.map((file) => (
                    <li key={file.id} className="flex items-center">
                      {selecting && (
                        <span className="ps-4">
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
                        </span>
                      )}
                      <RevisionListRow
                        modelId={modelId}
                        file={file}
                        jobs={jobs}
                        selected={file.id === selectedId}
                        onInspect={() => inspect(file.id)}
                      />
                    </li>
                  ))}
                </ul>
              </SoftCard>
            )}
          </InspectorSection>
          <div className="mt-5 space-y-3">
            {gcodeFiles.length === 0 && (
              <div className="space-y-2 text-sm text-muted-foreground">
                <p>{uiText("No sliced G-code revisions yet.")}</p>
                <p>{uiText("modelDetail.sliceThenAdd")}</p>
                {onFiles && (
                  <Button size="sm" variant="outline" onClick={onFiles}>
                    {uiText("View source files")}
                  </Button>
                )}
              </div>
            )}
            {!comparing &&
              !selecting &&
              gcodeFiles
                .filter((file) => file.id === selectedId)
                .map((f) => {
                  const isEditingRevision = editingRevisionId === f.id;
                  const uploadedTo = printerFilesByFileId.get(f.id) ?? [];
                  const revisionNumber = String(f.gcode_revision_number ?? f.version);
                  return (
                    <InspectorSection
                      key={f.id}
                      title={
                        <span
                          data-revision-id={f.id}
                          tabIndex={gcodeFiles.length === 1 ? 0 : undefined}
                        >
                          {uiText("Rev {value1}", { value1: revisionNumber })}
                        </span>
                      }
                      actions={
                        <Button
                          size="xs"
                          variant="ghost"
                          aria-label={uiText("Edit revision")}
                          disabled={!auth.isAuthenticated || !canEdit}
                          onClick={() => startRevisionEdit(f)}
                        >
                          {uiText("Edit revision")}
                        </Button>
                      }
                    >
                      <SoftCard>
                        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
                          {onPreview && (
                            <Button size="sm" variant="outline" onClick={() => onPreview(f.id)}>
                              {uiText("Preview G-code")}
                            </Button>
                          )}
                          <SlicerOpenButton
                            fileId={f.id}
                            fileType={
                              f.original_filename.toLowerCase().endsWith(".bgcode")
                                ? "bgcode"
                                : f.file_type
                            }
                            size="sm"
                            showLabel
                            outline
                          />
                          <DropdownMenu
                            open={actionsFileId === f.id}
                            onOpenChange={(open) => setActionsFileId(open ? f.id : null)}
                            portal
                            align="end"
                            className="ms-auto"
                            trigger={
                              <Button
                                variant="outline"
                                size="icon-sm"
                                className="size-[34px]"
                                data-menu-trigger
                                aria-haspopup="menu"
                                aria-expanded={actionsFileId === f.id}
                                aria-label={uiText("More actions for Rev {value1}", {
                                  value1: revisionNumber,
                                })}
                                onClick={() =>
                                  setActionsFileId(actionsFileId === f.id ? null : f.id)
                                }
                              >
                                <MoreVertical className="size-4" aria-hidden />
                              </Button>
                            }
                            contentClassName="w-52 border border-border bg-popover p-1 shadow-lg"
                          >
                            <MenuItem
                              onSelect={() => {
                                setActionsFileId(null);
                                void downloadAuthenticatedFile(
                                  `/api/v1/files/${f.id}/download`,
                                  f.original_filename,
                                ).catch((error) => toast.error(error));
                              }}
                            >
                              {uiText("Download G-code")}
                            </MenuItem>
                            {onLogPrint && (
                              <MenuItem
                                onSelect={() => {
                                  setActionsFileId(null);
                                  onLogPrint(f.id, "completed");
                                }}
                              >
                                {uiText("modelDetail.recordResult")}
                              </MenuItem>
                            )}
                            {!f.is_recommended && canEdit && (
                              <MenuItem
                                disabled={saving === f.id}
                                onSelect={() => {
                                  setActionsFileId(null);
                                  void update(f, { is_recommended: true });
                                }}
                              >
                                {uiText("Set as recommended")}
                              </MenuItem>
                            )}
                            {canEdit && (
                              <MenuItem
                                onSelect={() => {
                                  setActionsFileId(null);
                                  setTagsDialog((current) => ({
                                    fileId: f.id,
                                    opened: current.opened + 1,
                                  }));
                                }}
                              >
                                {f.tags.length ? uiText("Edit tags") : uiText("Add tags")}
                              </MenuItem>
                            )}
                            {gcodeFiles.length > 1 && (
                              <MenuItem
                                disabled={!auth.isAuthenticated || !canEdit}
                                onSelect={() => {
                                  setActionsFileId(null);
                                  setSelecting(true);
                                  setSelectedRevisionIds(new Set());
                                }}
                              >
                                {uiText("Edit labels")}
                              </MenuItem>
                            )}
                            <hr role="separator" className="my-1 border-border" />
                            {canEdit && f.revision_status !== "archived" && (
                              <MenuItem
                                destructive
                                disabled={saving === f.id}
                                onSelect={() => {
                                  setActionsFileId(null);
                                  void update(f, { revision_status: "archived" });
                                }}
                              >
                                {uiText("Archive revision")}
                              </MenuItem>
                            )}
                            <MenuItem
                              destructive
                              disabled={!auth.isAuthenticated || !canEdit || saving === f.id}
                              onSelect={() => {
                                setActionsFileId(null);
                                deleteRevision(f);
                              }}
                            >
                              <Trash2 className="size-4" aria-hidden />
                              {uiText("Move to trash")}
                            </MenuItem>
                          </DropdownMenu>
                        </div>
                        <PropertyList label={uiText("Revision facts")}>
                          <EstimateRow modelId={modelId} file={f} />
                          <PropertyRow label={uiText("Material")}>
                            {f.metadata?.material_type ?? (
                              <span className="text-muted-foreground">
                                {uiText("Not in the file")}
                              </span>
                            )}
                          </PropertyRow>
                          <PropertyRow label={uiText("Printed")}>
                            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                              <RevisionResults fileId={f.id} jobs={jobs} />
                              {onActivity && (
                                <Button
                                  size="xs"
                                  variant="ghost"
                                  className="h-auto p-0"
                                  onClick={() => onActivity(f.id)}
                                >
                                  {uiText("modelDetail.viewActivity")}
                                </Button>
                              )}
                            </div>
                          </PropertyRow>
                          {f.revision_notes && (
                            <PropertyRow label={uiText("Notes")}>{f.revision_notes}</PropertyRow>
                          )}
                          <PropertyRow label={uiText("File")}>
                            <div className="min-w-0 space-y-0.5">
                              <p className="wrap-anywhere">{f.original_filename}</p>
                              <p className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
                                <span>
                                  {f.file_type === "3mf" ||
                                  f.original_filename.toLowerCase().endsWith(".3mf")
                                    ? uiText("Sliced 3MF")
                                    : uiText("G-code")}{" "}
                                  · {formatBytes(f.size_bytes)}
                                </span>
                                <HashValue
                                  sha={f.sha256}
                                  label={uiText("File SHA-256")}
                                  filename={f.original_filename}
                                />
                              </p>
                            </div>
                          </PropertyRow>
                          {uploadedTo.length > 0 && (
                            <PropertyRow label={uiText("On printers")}>
                              {uploadedTo.map((row) => (
                                <span
                                  key={`${row.printer_id}-${row.remote_filename}`}
                                  className="inline-flex items-center gap-1 text-xs text-muted-foreground"
                                >
                                  <Wifi className="size-3" aria-hidden />
                                  {row.printer_name}
                                </span>
                              ))}
                            </PropertyRow>
                          )}
                        </PropertyList>
                        {onSettings && (
                          <SettingsLink modelId={modelId} file={f} onSettings={onSettings} />
                        )}
                      </SoftCard>
                      {tagsDialog.fileId === f.id && (
                        <EntityTagsDialog
                          key={`${f.id}-${tagsDialog.opened}`}
                          initialOpen
                          triggerMode="none"
                          entityLabel={f.original_filename}
                          tags={f.tags}
                          availableTags={availableTags}
                          canEdit={canEdit}
                          help={uiText(
                            "Artifact tags make the owning Model discoverable without changing the Model’s direct tags.",
                          )}
                          onSave={(tags) => saveArtifactTags(f, tags)}
                        />
                      )}
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
                    </InspectorSection>
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
