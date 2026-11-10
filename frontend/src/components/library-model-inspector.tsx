"use client";

import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Check, ChevronRight, Loader2, Plus, Star, X } from "lucide-react";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import {
  modelPrinterFilesOptions,
  useModelDetail,
  useModelPrintJobs,
} from "@/features/library/model-detail";
import { refreshLibraryMetadata } from "@/features/library/metadata";
import {
  createManualPrintJob,
  downloadAuthenticatedFile,
  getAssetUrl,
  getModel,
  updateModel,
} from "@/lib/api";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useAuth } from "@/lib/auth-context";
import { usePrinters } from "@/lib/queries";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { formatBytes, formatDuration, formatGrams, formatNumber, timeAgo } from "@/lib/format";
import { ApiError, userMessage } from "@/lib/errors";
import { isTypingTarget, keyboardOverlayOpen } from "@/lib/keyboard-commands";
import { toast } from "@/lib/toast";
import { Button } from "@/components/ui/button";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { AddGcodeRevisionModal } from "@/components/model-detail/add-revision-modal";
import { SendToButtons } from "@/components/model-detail/send-to-buttons";
import { HashValue } from "@/components/model-detail/file-hashes";
import { normalizeRecommendedGcodeFiles } from "@/components/model-detail/presentation";
import { useRevisionUpdater } from "@/components/model-detail/use-revision-updater";
import type { FileRead, MetadataRead, ModelRead } from "@/types";
import "./library-model-inspector.css";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);
const GcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then((m) => ({ default: m.GcodeViewer })),
);
const meshTypes = new Set(["stl", "3mf", "obj", "step", "glb", "gltf", "ply", "3dm", "fbx"]);

export function LibraryModelInspector({
  id,
  open,
  origin,
  canWrite,
  onClose,
}: {
  id: number;
  open: boolean;
  origin?: LibraryEntry;
  canWrite: boolean;
  onClose: () => void;
}) {
  useUiLocale();
  const detail = useModelDetail(id);
  const title = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    if (open) title.current?.focus({ preventScroll: true });
  }, [id, open]);
  return (
    <aside
      hidden={!open}
      id="model-drawer"
      aria-label={uiText("Model details")}
      className="library-model-inspector"
    >
      <header className="library-inspector-header">
        <h2 ref={title} tabIndex={-1} title={detail.data?.name}>
          {detail.data?.name ?? uiText("Model details")}
        </h2>
        <LibraryItemLink
          origin={origin}
          href={`/models/${id}`}
          className="library-inspector-open-page"
          title={uiText("Open the full model page")}
        >
          {uiText("Open page")}
        </LibraryItemLink>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={uiText("Close details")}
          onClick={onClose}
        >
          <X className="h-4 w-4" />
        </Button>
      </header>
      {detail.isError || !detail.active ? (
        <div className="library-inspector-body" role="alert">
          <p>{detail.error ? userMessage(detail.error) : uiText("Sign in to view this model")}</p>
          <Button variant="outline" size="sm" onClick={() => void detail.refetch()}>
            {uiText("Retry")}
          </Button>
        </div>
      ) : detail.data ? (
        <InspectorContent
          key={`${id}:${origin?.session ?? getSessionVersion()}`}
          model={detail.data}
          publish={detail.publish}
          active={open}
          canWrite={canWrite}
          onClose={onClose}
        />
      ) : (
        <div className="library-inspector-body" role="status">
          <Loader2 className="h-5 w-5 animate-spin" />
          <span className="sr-only">{uiText("Loading...")}</span>
        </div>
      )}
    </aside>
  );
}

function InspectorContent({
  model,
  publish,
  active,
  canWrite,
  onClose,
}: {
  model: ModelRead;
  active: boolean;
  publish: ReturnType<typeof useModelDetail>["publish"];
  canWrite: boolean;
  onClose: () => void;
}) {
  const { user } = useAuth();
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const canEdit = canWrite && (model.effective_role === "edit" || model.effective_role === "admin");
  const administrator = !!user?.is_superuser;
  const history = useModelPrintJobs(model.id, administrator);
  const printersQuery = usePrinters({ enabled: administrator });
  const printers = printersQuery.data ?? [];
  const files = normalizeRecommendedGcodeFiles(
    [...model.files].sort((a, b) => a.version - b.version),
  );
  const revisions = files.filter((file) => file.file_type === "gcode");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const file =
    revisions.find((item) => item.id === selectedId) ??
    revisions.find((item) => item.is_recommended) ??
    revisions.at(-1);
  const [printerId, setPrinterId] = useState<number | null>(null);
  const printer =
    printers.find((item) => item.id === printerId) ??
    printers.find((item) => item.is_default) ??
    printers[0];
  const [tab, setTab] = useState<"print" | "history" | "settings">("print");
  const [failing, setFailing] = useState(false);
  const [failNote, setFailNote] = useState("");
  const [busy, setBusy] = useState(false);
  const commandBusy = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const failureInput = useRef<HTMLTextAreaElement>(null);
  const failedButton = useRef<HTMLButtonElement>(null);
  const workspace = useRef<HTMLElement>(null);
  const [addOpen, setAddOpen] = useState(false);
  const [sendOpen, setSendOpen] = useState(false);
  const printerFiles = useQuery({
    ...modelPrinterFilesOptions(model.id),
    enabled: administrator && sendOpen,
  });
  const revision = useRevisionUpdater(model.id, publish);
  const [descriptionIntent, setDescriptionIntent] = useState<{
    base: ModelRead;
    text: string;
  } | null>(null);
  const [descriptionBusy, setDescriptionBusy] = useState(false);
  const descriptionBusyRef = useRef(false);
  const [descriptionProblem, setDescriptionProblem] = useState<"conflict" | "unconfirmed" | null>(
    null,
  );
  const [descriptionReviewed, setDescriptionReviewed] = useState<ModelRead | null>(null);
  const [descriptionReviewing, setDescriptionReviewing] = useState(false);
  const [descriptionDenied, setDescriptionDenied] = useState(false);
  const descriptionGeneration = useRef(0);
  const descriptionRequest = useRef<AbortController | null>(null);
  const [descriptionReviewActive, setDescriptionReviewActive] = useState(active);
  if (descriptionReviewActive !== active) {
    setDescriptionReviewActive(active);
    if (!active) setDescriptionReviewing(false);
  }
  useEffect(() => {
    if (!active) {
      descriptionRequest.current?.abort();
    }
  }, [active]);
  useEffect(
    () => () => {
      descriptionGeneration.current += 1;
      descriptionRequest.current?.abort();
    },
    [],
  );
  function beginDescription() {
    descriptionGeneration.current += 1;
    descriptionRequest.current?.abort();
    setDescriptionIntent({ base: model, text: model.description ?? "" });
    setDescriptionProblem(null);
    setDescriptionReviewed(null);
    setDescriptionDenied(false);
    setDescriptionReviewing(false);
  }
  function cancelDescription() {
    if (descriptionBusyRef.current) return;
    descriptionGeneration.current += 1;
    descriptionRequest.current?.abort();
    setDescriptionIntent(null);
    setDescriptionProblem(null);
    setDescriptionReviewed(null);
    setDescriptionReviewing(false);
  }
  const [preview, setPreview] = useState<"3d" | "gcode">("3d");
  const sourceFile = files.find((item) => meshTypes.has(item.file_type));
  const geometry = sourceFile?.metadata;
  const jobs = (history.isError ? [] : (history.data ?? [])).filter(
    (job) => job.file_id === file?.id,
  );
  const onPrinter = jobs.filter((job) => job.printer_id === printer?.id);
  const ok = onPrinter.filter((job) => job.state === "completed").length;
  const failed = onPrinter.filter((job) => job.state === "failed").length;
  const canLog = canEdit && administrator && !!file && !!printer && !busy;
  const selectedFormat = file?.original_filename.toLowerCase().endsWith(".bgcode")
    ? "bgcode_binary"
    : "gcode_text";
  const canSend =
    canEdit &&
    administrator &&
    !!printer?.access.can_print &&
    printer.capabilities.can_upload &&
    printer.capabilities.accepted_print_formats.includes(selectedFormat);

  function cancelFailure() {
    setFailing(false);
    setFailNote("");
    requestAnimationFrame(() => failedButton.current?.focus({ preventScroll: true }));
  }
  function beginFailure() {
    if (!canLog || commandBusy.current) return;
    setTab("print");
    setFailing(true);
    setFailNote("");
    setError(null);
    requestAnimationFrame(() => failureInput.current?.focus());
  }
  async function log(state: "completed" | "failed") {
    if (!canLog || !file || !printer || commandBusy.current) return;
    const intent = {
      modelId: model.id,
      fileId: file.id,
      printerId: printer.id,
      state,
      note: state === "failed" ? failNote.trim() : "",
    };
    commandBusy.current = true;
    setBusy(true);
    setError(null);
    try {
      requireSessionVersion(session);
      const job = await createManualPrintJob(intent.modelId, {
        file_id: intent.fileId,
        printer_id: intent.printerId,
        state: intent.state,
        error: intent.note || null,
      });
      requireSessionVersion(session);
      if (
        job.file_id !== intent.fileId ||
        job.printer_id !== intent.printerId ||
        job.state !== intent.state
      )
        throw new Error(uiText("Invalid print record acknowledgement"));
      await history.publish(job);
      requireSessionVersion(session);
      refreshLibraryMetadata(client, session);
      if (alive.current) {
        setFailing(false);
        setFailNote("");
        setTab("history");
        toast.success(uiText("Print record added"));
      }
    } catch (cause) {
      if (session === getSessionVersion() && alive.current) setError(userMessage(cause));
    } finally {
      commandBusy.current = false;
      if (session === getSessionVersion() && alive.current) setBusy(false);
    }
  }
  async function saveDescription(reviewedBase?: ModelRead) {
    if (
      !descriptionIntent ||
      !canEdit ||
      descriptionDenied ||
      descriptionBusyRef.current ||
      descriptionReviewing
    )
      return;
    if (descriptionProblem && !reviewedBase) return;
    if (
      reviewedBase &&
      (reviewedBase !== descriptionReviewed ||
        !["edit", "admin"].includes(reviewedBase.effective_role ?? ""))
    )
      return;
    const intent = { ...descriptionIntent, base: reviewedBase ?? descriptionIntent.base };
    const generation = descriptionGeneration.current;
    descriptionBusyRef.current = true;
    setDescriptionBusy(true);
    setError(null);
    try {
      requireSessionVersion(session);
      const saved = await updateModel(
        model.id,
        { description: intent.text.trim() || null },
        intent.base,
      );
      requireSessionVersion(session);
      const accepted = await publish(saved);
      if (!alive.current || generation !== descriptionGeneration.current) return;
      if (accepted) {
        setDescriptionIntent(null);
        setDescriptionProblem(null);
        setDescriptionReviewed(null);
      } else {
        setDescriptionProblem("unconfirmed");
        setDescriptionReviewed(null);
      }
    } catch (cause) {
      if (
        session !== getSessionVersion() ||
        !alive.current ||
        generation !== descriptionGeneration.current
      )
        return;
      if (cause instanceof ApiError && cause.status === 412) {
        setDescriptionProblem("conflict");
        setDescriptionReviewed(null);
      } else if (
        !(cause instanceof ApiError) ||
        cause.status === 0 ||
        cause.status === 408 ||
        cause.status >= 500
      ) {
        setDescriptionProblem("unconfirmed");
        setDescriptionReviewed(null);
      } else {
        setError(userMessage(cause));
        if ([401, 403, 404].includes(cause.status)) setDescriptionDenied(true);
      }
    } finally {
      descriptionBusyRef.current = false;
      if (session === getSessionVersion() && alive.current) setDescriptionBusy(false);
    }
  }
  async function reviewDescription() {
    if (
      !descriptionIntent ||
      descriptionReviewing ||
      descriptionBusyRef.current ||
      session !== getSessionVersion()
    )
      return;
    const generation = descriptionGeneration.current;
    const controller = new AbortController();
    descriptionRequest.current?.abort();
    descriptionRequest.current = controller;
    setDescriptionReviewing(true);
    setDescriptionReviewed(null);
    setError(null);
    try {
      const latest = await getModel(model.id, { signal: controller.signal });
      requireSessionVersion(session);
      if (
        !alive.current ||
        controller.signal.aborted ||
        generation !== descriptionGeneration.current
      )
        return;
      if (latest.id !== model.id) throw new Error(uiText("Couldn’t load this model"));
      setDescriptionReviewed(latest);
      setDescriptionDenied(latest.effective_role !== "edit" && latest.effective_role !== "admin");
    } catch (cause) {
      if (
        session === getSessionVersion() &&
        alive.current &&
        !controller.signal.aborted &&
        generation === descriptionGeneration.current
      ) {
        setError(userMessage(cause));
        if (cause instanceof ApiError && [401, 403, 404].includes(cause.status))
          setDescriptionDenied(true);
      }
    } finally {
      if (
        session === getSessionVersion() &&
        alive.current &&
        generation === descriptionGeneration.current
      )
        setDescriptionReviewing(false);
    }
  }
  async function useLatestDescription() {
    if (!descriptionReviewed || descriptionBusyRef.current || !descriptionIntent) return;
    const latest = descriptionReviewed;
    const generation = descriptionGeneration.current;
    if (session !== getSessionVersion()) return;
    const accepted = await publish(latest);
    if (
      session !== getSessionVersion() ||
      !alive.current ||
      generation !== descriptionGeneration.current
    )
      return;
    if (!accepted) {
      setDescriptionReviewed(null);
      return;
    }
    setDescriptionIntent({ base: latest, text: latest.description ?? "" });
    setDescriptionProblem(null);
    setDescriptionReviewed(null);
  }
  useEffect(() => {
    function keydown(event: KeyboardEvent) {
      if (
        !active ||
        event.repeat ||
        event.defaultPrevented ||
        event.isComposing ||
        event.getModifierState("AltGraph") ||
        keyboardOverlayOpen()
      )
        return;
      if (event.key === "Escape" && failing) {
        event.preventDefault();
        event.stopImmediatePropagation();
        if (!commandBusy.current) cancelFailure();
        return;
      }
      if (isTypingTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onClose();
        return;
      }
      if (
        !failing &&
        workspace.current?.contains(document.activeElement) &&
        (event.key === "1" || event.key === "2") &&
        canLog
      ) {
        event.preventDefault();
        if (event.key === "1") void log("completed");
        else beginFailure();
      }
    }
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  });
  function selectRevision(id: number) {
    setSelectedId(id);
    setFailing(false);
    setFailNote("");
    setError(null);
  }

  return (
    <div className="library-inspector-body">
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      {revisions.length ? (
        <section
          id="rev-section"
          ref={workspace}
          aria-label={uiText("G-code revisions")}
          className="library-inspector-workspace"
        >
          <header className="library-inspector-section-heading">
            <h3>
              {uiText("G-code revisions")} <span>{revisions.length}</span>
            </h3>
            <Button
              size="xs"
              variant="outline"
              disabled={!canEdit}
              onClick={() => setAddOpen(true)}
            >
              <Plus className="h-3.5 w-3.5" />
              {uiText("Add revision")}
            </Button>
          </header>
          <div
            role="tablist"
            aria-label={uiText("Revision details")}
            className="library-inspector-tabs"
            onKeyDown={(event) => {
              const tabs = Array.from(
                event.currentTarget.querySelectorAll<HTMLButtonElement>('[role="tab"]'),
              );
              const index = tabs.findIndex((button) => button === document.activeElement);
              const next =
                event.key === "ArrowRight"
                  ? index + 1
                  : event.key === "ArrowLeft"
                    ? index - 1
                    : event.key === "Home"
                      ? 0
                      : event.key === "End"
                        ? tabs.length - 1
                        : null;
              if (next === null || index < 0) return;
              event.preventDefault();
              const target = tabs[(next + tabs.length) % tabs.length];
              target?.click();
              target?.focus();
            }}
          >
            {(["print", "history", "settings"] as const).map((value) => (
              <button
                key={value}
                id={`inspector-tab-${value}`}
                type="button"
                role="tab"
                aria-selected={tab === value}
                aria-controls={`inspector-pane-${value}`}
                tabIndex={tab === value ? 0 : -1}
                onClick={() => setTab(value)}
              >
                {value === "print" ? (
                  uiText("Print")
                ) : value === "history" ? (
                  <>
                    {uiText("History")} <span>{jobs.length}</span>
                  </>
                ) : (
                  uiText("Settings")
                )}
              </button>
            ))}
          </div>
          {file && (
            <>
              <section
                role="tabpanel"
                id="inspector-pane-print"
                aria-labelledby="inspector-tab-print"
                hidden={tab !== "print"}
              >
                <div className="library-inspector-overview">
                  {revisions.length > 1 ? (
                    <select
                      disabled={busy}
                      value={file.id}
                      aria-label={uiText("Choose G-code revision")}
                      onChange={(event) => selectRevision(Number(event.target.value))}
                    >
                      {revisions.map((item) => (
                        <option key={item.id} value={item.id}>
                          {revisionName(item)}
                          {item.is_recommended ? ` · ★ ${uiText("Recommended")}` : ""}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <div className="library-inspector-single">
                      <strong>{revisionName(file)}</strong>
                      {file.is_recommended && (
                        <span>
                          <Star className="h-3 w-3 fill-current" />
                          {uiText("Recommended")}
                        </span>
                      )}
                    </div>
                  )}
                  {!file.is_recommended && (
                    <Button
                      variant="ghost"
                      size="xs"
                      disabled={!canEdit || revision.saving !== null}
                      onClick={() => void revision.update(file, { is_recommended: true })}
                    >
                      {uiText("Recommend")}
                    </Button>
                  )}
                  <dl className="library-inspector-properties">
                    <div>
                      <dt>{uiText("Result")}</dt>
                      <dd>
                        {!administrator ? (
                          uiText("Access restricted")
                        ) : history.isPending ? (
                          uiText("Loading...")
                        ) : history.isError ? (
                          <Button size="xs" variant="ghost" onClick={() => void history.refetch()}>
                            {uiText("Retry")}
                          </Button>
                        ) : (
                          <>
                            <span className="font-mono">
                              {ok ? uiText("inspector.okCount", { count: ok }) : null}
                              {ok && failed ? " · " : null}
                              {failed ? (
                                <span className="text-destructive">
                                  {uiText("inspector.failedCount", { count: failed })}
                                </span>
                              ) : null}
                              {!ok && !failed ? uiText("No recorded prints") : null}
                            </span>
                            {onPrinter[0]?.error && (
                              <p className="mt-1 break-words">{onPrinter[0].error}</p>
                            )}
                          </>
                        )}
                      </dd>
                    </div>
                    <div>
                      <dt>{uiText("Sliced for")}</dt>
                      <dd>{file.metadata?.printer_model ?? uiText("Not recorded")}</dd>
                    </div>
                    <div>
                      <dt>{uiText("File")}</dt>
                      <dd className="font-mono">{revisionFacts(file.metadata)}</dd>
                    </div>
                  </dl>
                </div>
                <section
                  className="library-inspector-print"
                  aria-label={uiText("Print this revision")}
                >
                  <label className="library-inspector-control-row">
                    <span>{uiText("Printer")}</span>
                    <select
                      value={printer?.id ?? ""}
                      disabled={!administrator || busy || printersQuery.isError}
                      aria-label={uiText("Printer for printing and logging")}
                      onChange={(event) => {
                        setPrinterId(Number(event.target.value));
                        setFailing(false);
                        setFailNote("");
                      }}
                    >
                      <option value="" disabled>
                        {uiText("No printers")}
                      </option>
                      {printers.map((item) => (
                        <option key={item.id} value={item.id}>
                          {item.name}
                        </option>
                      ))}
                    </select>
                  </label>
                  {printersQuery.isError && (
                    <p className="library-inspector-caption" role="alert">
                      {userMessage(printersQuery.error)}
                    </p>
                  )}
                  {!canSend && (
                    <p className="library-inspector-caption">
                      {!canEdit
                        ? uiText("Read-only")
                        : !administrator
                          ? uiText("Access restricted")
                          : uiText("Download the G-code and transfer it to your printer.")}
                    </p>
                  )}
                  <div className="library-inspector-actions" data-library-revision-actions>
                    {canSend && (
                      <Button size="xs" onClick={() => setSendOpen(true)}>
                        {uiText("Send to printer")}
                      </Button>
                    )}
                    <Button
                      size="xs"
                      variant={canSend ? "outline" : "default"}
                      onClick={() =>
                        void downloadAuthenticatedFile(
                          `/api/v1/files/${file.id}/download`,
                          file.original_filename,
                        ).catch((cause) => toast.error(cause))
                      }
                    >
                      {uiText("Download G-code")}
                    </Button>
                  </div>
                </section>
                <section
                  className="library-inspector-results"
                  aria-label={uiText("Log print outcome")}
                >
                  <span>{uiText("Print outcome")}</span>
                  {!failing && (
                    <div className="library-inspector-actions" data-library-log-outcomes>
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={!canLog}
                        aria-keyshortcuts="1"
                        onClick={() => void log("completed")}
                      >
                        <Check className="h-3.5 w-3.5" />
                        {uiText("OK")}
                      </Button>
                      <Button
                        ref={failedButton}
                        size="xs"
                        variant="outline"
                        disabled={!canLog}
                        aria-keyshortcuts="2"
                        onClick={beginFailure}
                      >
                        <X className="h-3.5 w-3.5 text-destructive" />
                        {uiText("Failed")}
                      </Button>
                    </div>
                  )}
                  {failing && (
                    <div
                      className="library-inspector-failure"
                      role="group"
                      aria-label={uiText("Log a failed print")}
                    >
                      <p>{uiText("Log failed print")}</p>
                      <label htmlFor="inspector-failure-note">
                        {uiText("What went wrong?")}{" "}
                        <span className="text-muted-foreground">{uiText("Optional")}</span>
                      </label>
                      <textarea
                        ref={failureInput}
                        id="inspector-failure-note"
                        maxLength={300}
                        rows={2}
                        value={failNote}
                        disabled={busy || !canEdit || !administrator}
                        onChange={(event) => setFailNote(event.target.value)}
                        onKeyDown={(event) => {
                          if (
                            event.defaultPrevented ||
                            event.nativeEvent.isComposing ||
                            event.repeat ||
                            event.altKey ||
                            event.shiftKey ||
                            event.getModifierState("AltGraph") ||
                            !(event.metaKey || event.ctrlKey) ||
                            event.key !== "Enter"
                          )
                            return;
                          event.preventDefault();
                          void log("failed");
                        }}
                        placeholder={uiText("e.g. Corner lifted at layer 112")}
                      />
                      <div className="flex items-center justify-end gap-2">
                        <kbd className="mr-auto text-xs text-muted-foreground">
                          {navigator.platform.includes("Mac") ? "⌘" : uiText("Ctrl")}
                          {uiText("+Enter")}
                        </kbd>
                        <Button size="xs" variant="outline" disabled={busy} onClick={cancelFailure}>
                          {uiText("Cancel")}
                        </Button>
                        <Button
                          size="xs"
                          loading={busy}
                          disabled={!canLog}
                          onClick={() => void log("failed")}
                        >
                          {uiText("Save failed print")}
                        </Button>
                      </div>
                    </div>
                  )}
                </section>
              </section>
              <section
                role="tabpanel"
                id="inspector-pane-history"
                aria-labelledby="inspector-tab-history"
                hidden={tab !== "history"}
                className="library-inspector-history"
              >
                <div className="library-inspector-view-context">
                  <h4>{revisionName(file)}</h4>
                  <span>{uiText("Print history")}</span>
                </div>
                {!administrator ? (
                  <p>{uiText("Access restricted")}</p>
                ) : history.isPending ? (
                  <p>{uiText("Loading...")}</p>
                ) : history.isError ? (
                  <div role="alert">
                    <p>{userMessage(history.error)}</p>
                    <Button size="xs" variant="outline" onClick={() => void history.refetch()}>
                      {uiText("Retry")}
                    </Button>
                  </div>
                ) : jobs.length ? (
                  <ol>
                    {jobs.map((job) => (
                      <li key={job.id}>
                        <i data-state={job.state} />
                        <div>
                          <p>
                            <strong>
                              {job.state === "completed"
                                ? uiText("Completed")
                                : job.state === "failed"
                                  ? uiText("Failed")
                                  : job.state === "cancelled"
                                    ? uiText("Cancelled")
                                    : job.state}
                            </strong>{" "}
                            · {job.printer_name} · {timeAgo(job.finished_at ?? job.created_at)}
                          </p>
                          {job.error && <p className="text-foreground">{job.error}</p>}
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <p>{uiText("No print results recorded yet.")}</p>
                )}
              </section>
              <section
                role="tabpanel"
                id="inspector-pane-settings"
                aria-labelledby="inspector-tab-settings"
                hidden={tab !== "settings"}
                className="library-inspector-settings"
              >
                <div className="library-inspector-view-context">
                  <h4>{revisionName(file)}</h4>
                  <span>{uiText("All slicer settings")}</span>
                </div>
                <p className="library-inspector-caption">{timeAgo(file.uploaded_at)}</p>
                <p className="break-words font-mono text-xs">{file.original_filename}</p>
                <dl>
                  {settingFacts(file.metadata).map(([label, value]) => (
                    <div key={label}>
                      <dt>{label}</dt>
                      <dd>{value}</dd>
                    </div>
                  ))}
                </dl>
                {file.metadata?.slicer_name && (
                  <p className="library-inspector-caption">
                    {file.metadata.slicer_name} {file.metadata.slicer_version}
                  </p>
                )}
              </section>
            </>
          )}
        </section>
      ) : (
        <section className="library-inspector-empty">
          <strong>{uiText("No G-code revisions yet")}</strong>
          <p>{uiText("Slice this model in your slicer, then add the G-code file here.")}</p>
          <Button size="sm" disabled={!canEdit} onClick={() => setAddOpen(true)}>
            {uiText("Add G-code revision")}
          </Button>
        </section>
      )}
      <div className="library-inspector-preview library-preview">
        <div
          role="radiogroup"
          aria-label={uiText("Preview")}
          onKeyDown={(event) => {
            if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(event.key)) return;
            const controls = Array.from(
              event.currentTarget.querySelectorAll<HTMLButtonElement>(
                '[role="radio"]:not(:disabled)',
              ),
            );
            const index = controls.findIndex((button) => button === document.activeElement);
            if (index < 0) return;
            event.preventDefault();
            const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1 : -1;
            const next = controls[(index + step + controls.length) % controls.length];
            next?.click();
            next?.focus();
          }}
        >
          <button
            type="button"
            role="radio"
            aria-checked={preview === "3d"}
            tabIndex={preview === "3d" ? 0 : -1}
            onClick={() => setPreview("3d")}
          >
            {uiText("3D")}
          </button>
          <button
            type="button"
            role="radio"
            aria-checked={preview === "gcode"}
            tabIndex={preview === "gcode" ? 0 : -1}
            disabled={!file}
            onClick={() => setPreview("gcode")}
          >
            {uiText("G-code")}
          </button>
        </div>
        <Suspense fallback={<Loader2 className="h-5 w-5 animate-spin" />}>
          {preview === "gcode" && file ? (
            <GcodeViewer
              url={`/api/v1/files/${file.id}/toolpath`}
              showDimensions={false}
              screenshotName={model.slug}
            />
          ) : sourceFile ? (
            <STLViewer url={getAssetUrl(`/api/v1/files/${sourceFile.id}/stl`)} modelId={model.id} />
          ) : (
            <ProtectedThumbnail
              path={model.thumbnail_url}
              alt={model.name}
              className="h-full w-full"
              imageClassName="h-full w-full object-contain"
              placeholder={<Box className="h-7 w-7 text-muted-foreground" />}
            />
          )}
        </Suspense>
      </div>
      <section className="library-inspector-section">
        <header className="library-inspector-section-heading">
          <h3>{uiText("Description")}</h3>
          {canEdit && !descriptionIntent && (
            <Button
              size="xs"
              variant="outline"
              aria-label={uiText("Edit description")}
              onClick={beginDescription}
            >
              {model.description ? uiText("Edit") : uiText("Add")}
            </Button>
          )}
        </header>
        {descriptionIntent ? (
          <div className="space-y-2 pb-3">
            {descriptionProblem && (
              <div role="alert" className="space-y-2 border border-border bg-muted p-3 text-xs">
                <p>
                  {uiText(
                    descriptionProblem === "conflict"
                      ? "library.editConflict"
                      : "library.saveUnconfirmed",
                  )}
                </p>
                <Button
                  size="xs"
                  variant="outline"
                  loading={descriptionReviewing}
                  disabled={descriptionBusy}
                  onClick={() => void reviewDescription()}
                >
                  {uiText("library.reviewLatest")}
                </Button>
                {descriptionReviewed && (
                  <div className="space-y-2">
                    <strong>{uiText("Description")}</strong>
                    <p className="whitespace-pre-wrap break-words">
                      {descriptionReviewed.description ?? uiText("Not recorded")}
                    </p>
                    <div className="flex flex-wrap gap-2">
                      <Button
                        size="xs"
                        variant="outline"
                        disabled={descriptionBusy}
                        onClick={() => void useLatestDescription()}
                      >
                        {uiText("library.useLatest")}
                      </Button>
                      <Button
                        size="xs"
                        disabled={!canEdit || descriptionDenied || descriptionBusy}
                        onClick={() => void saveDescription(descriptionReviewed)}
                      >
                        {uiText("library.retryDraft")}
                      </Button>
                    </div>
                  </div>
                )}
              </div>
            )}
            <label className="sr-only" htmlFor="inspector-description">
              {uiText("Description")}
            </label>
            <textarea
              id="inspector-description"
              autoFocus
              maxLength={500}
              rows={3}
              value={descriptionIntent.text}
              disabled={descriptionBusy || descriptionReviewing || !canEdit || descriptionDenied}
              onChange={(event) =>
                setDescriptionIntent({ ...descriptionIntent, text: event.target.value })
              }
              onKeyDown={(event) => {
                if (
                  event.defaultPrevented ||
                  event.nativeEvent.isComposing ||
                  event.repeat ||
                  event.getModifierState("AltGraph")
                )
                  return;
                if (
                  event.key === "Enter" &&
                  (event.metaKey || event.ctrlKey) &&
                  !event.altKey &&
                  !event.shiftKey
                ) {
                  event.preventDefault();
                  void saveDescription();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  if (!descriptionBusy) cancelDescription();
                }
              }}
            />
            <div className="flex items-center justify-end gap-2">
              <span className="mr-auto text-xs text-muted-foreground">
                {descriptionIntent.text.length} /500
              </span>
              <Button
                size="xs"
                variant="outline"
                disabled={descriptionBusy}
                onClick={cancelDescription}
              >
                {uiText("Cancel")}
              </Button>
              <Button
                size="xs"
                loading={descriptionBusy}
                disabled={
                  !canEdit ||
                  descriptionDenied ||
                  descriptionReviewing ||
                  descriptionProblem !== null
                }
                onClick={() => void saveDescription()}
              >
                {uiText("Save")}
              </Button>
            </div>
          </div>
        ) : (
          <p
            className={`library-inspector-description ${model.description ? "" : "text-muted-foreground"}`}
          >
            {model.description ??
              uiText("No description yet. Add orientation, hardware or print notes.")}
          </p>
        )}
      </section>
      <details className="library-inspector-section">
        <summary className="library-inspector-section-heading">
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          <h3>{uiText("Geometry")}</h3>
        </summary>
        <dl className="library-inspector-geometry">
          {geometryFacts(geometry).map(([label, value]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
      </details>
      <details className="library-inspector-section">
        <summary className="library-inspector-section-heading">
          <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
          <h3>
            {uiText("Files")} <span>{files.length}</span>
          </h3>
          <span className="text-xs text-muted-foreground">{uiText("SHA-256")}</span>
        </summary>
        <ul className="library-inspector-files">
          {files.map((item) => (
            <li key={item.id}>
              <span className="library-inspector-file-type">{item.file_type.toUpperCase()}</span>
              <div>
                <strong title={item.original_filename}>{item.original_filename}</strong>
                <p>
                  {formatBytes(item.size_bytes)} ·{" "}
                  {item.file_type === "gcode" ? revisionName(item) : uiText("Source")}
                </p>
              </div>
              <HashValue
                sha={item.sha256}
                label={uiText("File SHA-256")}
                filename={item.original_filename}
              />
            </li>
          ))}
        </ul>
      </details>
      {addOpen && canEdit && (
        <AddGcodeRevisionModal
          modelId={model.id}
          modelName={model.name}
          currentRecommendedRevision={
            revisions.find((item) => item.is_recommended)?.gcode_revision_number ?? undefined
          }
          onClose={() => setAddOpen(false)}
          onUploaded={(saved) => {
            if (session !== getSessionVersion()) return;
            void publish(saved).then((accepted) => {
              if (accepted && alive.current) setAddOpen(false);
            });
          }}
        />
      )}
      {sendOpen && canEdit && administrator && (
        <div className="hidden">
          <SendToButtons
            modelName={model.name}
            gcodeFiles={revisions}
            printerFiles={printerFiles.data ?? []}
            open
            onOpenChange={setSendOpen}
            preselectFileId={file?.id}
            preselectPrinterId={printer?.id}
            compact
          />
        </div>
      )}
    </div>
  );
}
function revisionName(file: FileRead) {
  return `${uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) })}${file.revision_label ? ` · ${file.revision_label}` : ""}`;
}
function revisionFacts(meta: MetadataRead | null) {
  return (
    [
      meta?.estimated_time_s != null ? formatDuration(meta.estimated_time_s) : null,
      meta?.filament_weight_g != null ? formatGrams(meta.filament_weight_g) : null,
      meta?.layer_height_mm != null ? `${formatNumber(meta.layer_height_mm)} mm` : null,
      meta?.material_type,
    ]
      .filter(Boolean)
      .join(" · ") || uiText("Not recorded")
  );
}
function settingFacts(meta: MetadataRead | null): [string, string][] {
  return [
    [
      uiText("Time"),
      meta?.estimated_time_s != null
        ? formatDuration(meta.estimated_time_s)
        : uiText("Not recorded"),
    ],
    [
      uiText("Filament"),
      meta?.filament_weight_g != null
        ? formatGrams(meta.filament_weight_g)
        : uiText("Not recorded"),
    ],
    [
      uiText("Layer height"),
      meta?.layer_height_mm != null
        ? `${formatNumber(meta.layer_height_mm)} mm`
        : uiText("Not recorded"),
    ],
    [uiText("Material"), meta?.material_type ?? uiText("Not recorded")],
    [
      uiText("Nozzle"),
      meta?.nozzle_diameter_mm != null
        ? `${formatNumber(meta.nozzle_diameter_mm)} mm`
        : uiText("Not recorded"),
    ],
    [
      uiText("Infill"),
      meta?.infill_percent != null
        ? `${formatNumber(meta.infill_percent)}%`
        : uiText("Not recorded"),
    ],
    [
      uiText("Nozzle temperature"),
      meta?.nozzle_temperature_c != null
        ? `${formatNumber(meta.nozzle_temperature_c)} °C`
        : uiText("Not recorded"),
    ],
    [
      uiText("Bed temperature"),
      meta?.bed_temperature_c != null
        ? `${formatNumber(meta.bed_temperature_c)} °C`
        : uiText("Not recorded"),
    ],
    [uiText("Printer profile"), meta?.printer_model ?? uiText("Not recorded")],
  ];
}
function geometryFacts(meta: MetadataRead | null | undefined): [string, string][] {
  return [
    [
      uiText("Dimensions"),
      meta?.bbox_x_mm != null && meta.bbox_y_mm != null && meta.bbox_z_mm != null
        ? `${formatNumber(meta.bbox_x_mm)} × ${formatNumber(meta.bbox_y_mm)} × ${formatNumber(meta.bbox_z_mm)} mm`
        : uiText("Not recorded"),
    ],
    [
      uiText("Volume"),
      meta?.volume_mm3 != null
        ? `${formatNumber(meta.volume_mm3 / 1000)} cm³`
        : uiText("Not recorded"),
    ],
    [
      uiText("Triangles"),
      meta?.triangle_count != null ? formatNumber(meta.triangle_count) : uiText("Not recorded"),
    ],
  ];
}
