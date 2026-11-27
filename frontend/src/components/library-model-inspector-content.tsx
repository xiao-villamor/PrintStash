"use client";

import { lazy, Suspense, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight, Box, Download, Loader2, MoreHorizontal } from "lucide-react";
import { Printer3D } from "@/components/ui/printer-3d";
import { modelPrinterFilesOptions, useModelDetail } from "@/features/library/model-detail";
import { downloadAuthenticatedFile, getAssetUrl, getModel, updateModel } from "@/lib/api";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useAuth } from "@/lib/auth-context";
import { usePrinters } from "@/lib/queries";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import {
  formatBytes,
  formatCalendarDate,
  formatDuration,
  formatGrams,
  formatNumber,
} from "@/lib/format";
import { ApiError, userMessage } from "@/lib/errors";
import { isTypingTarget, keyboardOverlayOpen } from "@/lib/keyboard-commands";
import { toast } from "@/lib/toast";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { Button } from "@/components/ui/button";
import { MarkdownView } from "@/components/markdown-view";
import { TabBar } from "@/components/ui/tabs";
import { SendToButtons } from "@/components/model-detail/send-to-buttons";
import { InspectorPreviewResize } from "./inspector-preview-resize";
import {
  meshGeometryFacts,
  useMeshGeometryMeasurement,
} from "@/components/model-detail/use-mesh-geometry-facts";
import { InspectorPreviewImages } from "@/components/model-preview";
import { coverImageKind, NO_PREVIEWS, publishedViews } from "@/types/previews";
import { normalizeRecommendedGcodeFiles } from "@/components/model-detail/presentation";
import { useRevisionUpdater } from "@/components/model-detail/use-revision-updater";
import type { FileRead, MetadataRead, ModelRead } from "@/types";

const STLViewer = lazy(() =>
  import("@/components/stl-viewer").then((m) => ({ default: m.STLViewer })),
);
type PreviewMode = "model" | "images";

const meshTypes = new Set(["stl", "3mf", "obj", "step", "glb", "gltf", "ply", "3dm", "fbx"]);

const sectionAction = "px-0 text-muted-foreground hover:bg-transparent hover:text-foreground";
const descriptionText = "text-field leading-5 text-muted-foreground";

export function InspectorContent({
  model,
  publish,
  active,
  canWrite,
  onClose,
  previewSize,
  widthHandle,
}: {
  model: ModelRead;
  active: boolean;
  publish: ReturnType<typeof useModelDetail>["publish"];
  canWrite: boolean;
  onClose: () => void;
  previewSize: React.ComponentProps<typeof InspectorPreviewResize>;
  widthHandle: React.ReactNode;
}) {
  useUiLocale();
  const { user } = useAuth();
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
  const [error, setError] = useState<string | null>(null);
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
  const [preview, setPreview] = useState<PreviewMode | null>(null);
  const [sourceId, setSourceId] = useState<number | null>(null);
  const [fileMenu, setFileMenu] = useState<number | null>(null);
  const viewer = useRef<HTMLDivElement>(null);
  const meshFiles = files.filter(
    (item) => meshTypes.has(item.file_type) && item.sliced_geometry !== "gcode_only",
  );
  const sourceFile = meshFiles.find((item) => item.id === sourceId) ?? meshFiles[0];
  const previews = model.previews ?? NO_PREVIEWS;
  const hasImages = Boolean(
    previews.large_cover_url || model.thumbnail_url || publishedViews(previews).length,
  );
  const imagesFirst = coverImageKind(previews, "full") === "photo";
  const showImages =
    !sourceFile || (hasImages && (preview === "images" || (!preview && imagesFirst)));
  const showTabs = Boolean(sourceFile && hasImages);
  const viewerLoading = (
    <span role="status" className="flex items-center justify-center">
      <Loader2 className="size-5 animate-spin text-muted-foreground" aria-hidden />
      <span className="sr-only">{uiText("Loading...")}</span>
    </span>
  );
  function download(item: FileRead) {
    void downloadAuthenticatedFile(
      `/api/v1/files/${item.id}/download`,
      item.original_filename,
    ).catch((error) => toast.error(userMessage(error)));
  }
  const measurement = useMeshGeometryMeasurement(sourceFile ?? null);
  const geometry = meshGeometryFacts(sourceFile ?? null, measurement);
  const printStats = file ? revisionStats(file.metadata) : [];
  const selectedFormat = file?.original_filename.toLowerCase().endsWith(".bgcode")
    ? "bgcode_binary"
    : "gcode_text";
  const canSend =
    canEdit &&
    administrator &&
    !!printer?.access.can_print &&
    printer.capabilities.can_upload &&
    printer.capabilities.accepted_print_formats.includes(selectedFormat);
  const printerControl = printer && (
    <label className="library-inspector-printer">
      <span>{uiText("Printer")}</span>
      <select
        value={printer.id}
        disabled={!administrator || printersQuery.isError}
        aria-label={uiText("Printer for printing and logging")}
        onChange={(event) => setPrinterId(Number(event.target.value))}
      >
        {printers.map((item) => (
          <option key={item.id} value={item.id}>
            {item.name}
          </option>
        ))}
      </select>
    </label>
  );

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
      if (isTypingTarget(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        onClose();
        return;
      }
    }
    window.addEventListener("keydown", keydown, true);
    return () => window.removeEventListener("keydown", keydown, true);
  });

  return (
    <div className="library-inspector-body fade-in">
      <div ref={viewer} role="region" aria-label={uiText("Preview")} className="relative">
        {widthHandle}
        <div
          className="library-inspector-preview library-preview"
          style={{ height: previewSize.height }}
        >
          {showTabs && (
            <TabBar
              tabs={[
                { key: "model", label: uiText("3D model") },
                { key: "images", label: uiText("Images") },
              ]}
              active={showImages ? "images" : "model"}
              onChange={setPreview}
              showIndicator={false}
              className="absolute left-3 top-3 z-10 w-fit max-w-full flex-wrap gap-0.5 rounded-lg border border-border bg-popover p-0.5"
              tabClassName="inline-flex h-7 items-center px-2.5 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              activeTabClassName="bg-accent text-accent-foreground"
            />
          )}
          <div
            className="absolute inset-0 flex items-center justify-center"
            role={showTabs ? "tabpanel" : undefined}
            aria-label={showTabs ? uiText(showImages ? "Images" : "3D model") : undefined}
          >
            <Suspense fallback={viewerLoading}>
              {!showImages && sourceFile ? (
                <STLViewer
                  url={getAssetUrl(`/api/v1/files/${sourceFile.id}/stl`)}
                  modelId={model.id}
                />
              ) : (
                <InspectorPreviewImages
                  name={model.name}
                  cover={model.thumbnail_url}
                  previews={previews}
                />
              )}
            </Suspense>
          </div>
        </div>
      </div>
      <InspectorPreviewResize {...previewSize} />
      {error && (
        <p role="alert" className="mb-3 text-xs text-destructive">
          {error}
        </p>
      )}
      {file && (
        <section aria-label={uiText("Print this revision")} className="library-inspector-workspace">
          <div className="library-inspector-block-head">
            <h3 className="library-inspector-eyebrow">
              {file.is_recommended ? uiText("Recommended print") : uiText("Revision")}
            </h3>
            {revisions.length > 1 ? (
              <select
                value={file.id}
                aria-label={uiText("Choose G-code revision")}
                title={revisionName(file)}
                onChange={(event) => setSelectedId(Number(event.target.value))}
              >
                {revisions.map((item) => (
                  <option key={item.id} value={item.id}>
                    {revisionName(item)}
                    {item.is_recommended ? ` · ${uiText("Recommended")}` : ""}
                  </option>
                ))}
              </select>
            ) : (
              <span className="library-inspector-rev-chip">{revisionName(file)}</span>
            )}
          </div>
          <div className="library-inspector-print-summary">
            {!file.is_recommended && canEdit && (
              <Button
                className={sectionAction}
                variant="ghost"
                size="xs"
                disabled={revision.saving !== null}
                onClick={() => void revision.update(file, { is_recommended: true })}
              >
                {uiText("Recommend")}
              </Button>
            )}
            {printStats.length > 0 && (
              <dl className="library-inspector-stats">
                {printStats.map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value}</dd>
                  </div>
                ))}
              </dl>
            )}
            {file.metadata?.printer_model && (
              <p className="library-inspector-caption wrap-anywhere">
                {uiText("Sliced for")}{" "}
                <span className="text-foreground">{file.metadata.printer_model}</span>
              </p>
            )}
          </div>
          <div className="library-inspector-print">
            {printerControl}
            {printersQuery.isError && (
              <p className="library-inspector-caption" role="alert">
                {userMessage(printersQuery.error)}
              </p>
            )}
            {printer && !canSend && (
              <p className="library-inspector-caption">
                {!canEdit
                  ? uiText("Read-only")
                  : !administrator
                    ? uiText("Access restricted")
                    : uiText("Download the G-code and transfer it to your printer.")}
              </p>
            )}
            <div className="library-inspector-delivery" data-library-revision-actions>
              {canSend && (
                <Button size="sm" className="flex-1" onClick={() => setSendOpen(true)}>
                  <Printer3D className="size-4 shrink-0" aria-hidden />
                  {uiText("Send to printer")}
                </Button>
              )}
              {canSend ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="w-[34px] px-0"
                  aria-label={uiText("Download G-code")}
                  title={uiText("Download G-code")}
                  onClick={() => download(file)}
                >
                  <Download className="size-4 shrink-0" aria-hidden />
                </Button>
              ) : (
                <Button size="sm" className="flex-1" onClick={() => download(file)}>
                  <Download className="size-4 shrink-0" aria-hidden />
                  {uiText("Download G-code")}
                </Button>
              )}
            </div>
          </div>
        </section>
      )}
      <section className="library-inspector-section">
        <header className="library-inspector-section-heading">
          <h3>{uiText("Description")}</h3>
          {canEdit && !descriptionIntent && (
            <Button
              className={sectionAction}
              size="xs"
              variant="ghost"
              aria-label={uiText("Edit description")}
              onClick={beginDescription}
            >
              {model.description ? uiText("Edit") : uiText("Add")}
            </Button>
          )}
        </header>
        {descriptionIntent ? (
          <div className="space-y-2">
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
                    <p className="whitespace-pre-wrap wrap-anywhere">
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
                        variant="outline"
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
              maxLength={4096}
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
                {descriptionIntent.text.length} /4096
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
                variant="outline"
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
        ) : model.description ? (
          <InspectorDescription description={model.description} />
        ) : (
          <p className={descriptionText}>
            {uiText("No description yet. Add orientation, hardware or print notes.")}
          </p>
        )}
      </section>
      <dl className="library-inspector-properties library-inspector-origin divide-y divide-border">
        {(model.collection_label ?? model.collection) && (
          <div>
            <dt>{uiText("Collection")}</dt>
            <dd>{model.collection_label ?? model.collection}</dd>
          </div>
        )}
        <div>
          <dt>{uiText("Created")}</dt>
          <dd>
            <time dateTime={model.created_at}>{formatCalendarDate(model.created_at)}</time>
          </dd>
        </div>
        {model.source_url && (
          <div>
            <dt>{uiText("Source")}</dt>
            <dd>
              <a
                href={model.source_url}
                target="_blank"
                rel="noreferrer noopener"
                title={model.source_url}
                className="inline-flex max-w-full items-center gap-1 text-foreground hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="min-w-0 truncate">{sourceHost(model.source_url)}</span>
                <ArrowUpRight aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
              </a>
            </dd>
          </div>
        )}
      </dl>
      {sourceFile && (
        <section className="library-inspector-section">
          <header className="library-inspector-section-heading">
            <h3>{uiText("Geometry")}</h3>
          </header>
          {geometry.emptyMessage ? (
            <p className="text-xs text-muted-foreground">{geometry.emptyMessage}</p>
          ) : (
            <dl className="library-inspector-properties divide-y divide-border">
              {geometry.facts.map(([label, value]) => (
                <div key={label}>
                  <dt>{label}</dt>
                  <dd className="font-mono">{value}</dd>
                </div>
              ))}
            </dl>
          )}
        </section>
      )}
      <section className="library-inspector-section">
        <header className="library-inspector-section-heading">
          <h3>
            {uiText("Files")} <span>{files.length}</span>
          </h3>
        </header>
        <ul className="library-inspector-files divide-y divide-border">
          {files.map((item) => {
            const canPreview = meshFiles.includes(item);
            return (
              <li key={item.id}>
                <span className="library-inspector-file-type">{item.file_type.toUpperCase()}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  className="h-auto min-w-0 justify-between py-1 text-left"
                  aria-label={`${uiText("Download")} ${item.original_filename}`}
                  onClick={() => download(item)}
                >
                  <span className="flex min-w-0 flex-col items-start">
                    <strong className="max-w-full" title={item.original_filename}>
                      {item.original_filename}
                    </strong>
                    <span className="text-muted-foreground">
                      {formatBytes(item.size_bytes)} ·{" "}
                      {item.file_type === "gcode" ? revisionName(item) : uiText("Source")}
                    </span>
                  </span>
                  <Download className="h-4 w-4 shrink-0" aria-hidden />
                </Button>
                <DropdownMenu
                  open={fileMenu === item.id}
                  onOpenChange={(open) => setFileMenu(open ? item.id : null)}
                  portal
                  trigger={
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-haspopup="menu"
                      aria-expanded={fileMenu === item.id}
                      aria-label={uiText("Actions for {value1}", {
                        value1: item.original_filename,
                      })}
                      onClick={() => setFileMenu(fileMenu === item.id ? null : item.id)}
                    >
                      <MoreHorizontal className="h-4 w-4" aria-hidden />
                    </Button>
                  }
                >
                  <button
                    type="button"
                    role="menuitem"
                    className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                    onClick={() => {
                      setFileMenu(null);
                      download(item);
                    }}
                  >
                    <Download className="h-4 w-4" aria-hidden />
                    {uiText("Download")}
                  </button>
                  {canPreview && (
                    <button
                      type="button"
                      role="menuitem"
                      className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted"
                      onClick={() => {
                        setFileMenu(null);
                        setSourceId(item.id);
                        setPreview("model");
                        viewer.current?.scrollIntoView({ block: "nearest" });
                      }}
                    >
                      <Box className="h-4 w-4" aria-hidden />
                      {uiText("Open in viewer")}
                    </button>
                  )}
                </DropdownMenu>
              </li>
            );
          })}
        </ul>
      </section>
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
function InspectorDescription({ description }: { description: string }) {
  const id = useId();
  const content = useRef<HTMLDivElement>(null);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  useLayoutEffect(() => {
    const element = content.current;
    if (!element) return;
    const measure = () => {
      const lineHeight = Number.parseFloat(getComputedStyle(element).lineHeight);
      setOverflows(element.scrollHeight > lineHeight * 4 + 1);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [description, expanded]);

  return (
    <div>
      <div
        id={id}
        ref={content}
        className={`${descriptionText} wrap-anywhere ${expanded ? "" : "line-clamp-4"}`}
      >
        <MarkdownView source={description} />
      </div>
      {overflows && (
        <Button
          size="xs"
          variant="ghost"
          className={sectionAction}
          aria-controls={id}
          aria-expanded={expanded}
          onClick={() => setExpanded(!expanded)}
        >
          {uiText(expanded ? "Show less" : "Show more")}
        </Button>
      )}
    </div>
  );
}

/** The link's host, which is what a person recognises; an unparsable link is shown whole. */
function sourceHost(url: string) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}
function revisionName(file: FileRead) {
  return `${uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) })}${file.revision_label ? ` · ${file.revision_label}` : ""}`;
}
function revisionStats(meta: MetadataRead | null): [string, string][] {
  const stats: [string, string | null][] = [
    [uiText("Time"), meta?.estimated_time_s != null ? formatDuration(meta.estimated_time_s) : null],
    [
      uiText("Weight"),
      meta?.filament_weight_g != null ? formatGrams(meta.filament_weight_g) : null,
    ],
    [
      uiText("Layer"),
      meta?.layer_height_mm != null ? `${formatNumber(meta.layer_height_mm)} mm` : null,
    ],
    [uiText("Material"), meta?.material_type ?? null],
  ];
  return stats.filter((entry): entry is [string, string] => entry[1] !== null);
}
