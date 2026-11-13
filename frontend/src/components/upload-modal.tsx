"use client";

import { useContentMotion } from "@/lib/use-content-motion";
import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { uiMessage } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useI18n, useUiLocale } from "@/lib/i18n";

import { useId, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { vaultConfigOptions } from "@/lib/queries/settings-config";
import { librarySourcesOptions } from "@/lib/queries/settings-library-sources";
import { ChevronDown, File as FileIcon, Loader2, Plus, Upload, X } from "lucide-react";
import { capturePendingImport } from "@/lib/api";
import { useCollectionLookup, useCollectionSearch, useTags } from "@/lib/queries";
import { CollectionPicker } from "@/components/collection-picker";
import { toast } from "@/lib/toast";
import { createTask } from "@/lib/task-center";
import { useRequireAuth } from "@/lib/use-require-auth";
import { useAuth } from "@/lib/auth-context";
import { formatBytes } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { ModalShell } from "@/components/ui/modal";
import { TabBar } from "@/components/ui/tabs";
import "./upload-modal.css";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useComboboxNav } from "@/lib/use-combobox-nav";
import {
  entriesFromDataTransfer,
  extensionOf,
  fileListToItems,
  GCODE_ACCEPT,
  isGcodeFile,
  isSourceFile,
  mergeBulkItems,
  walkEntries,
  ARCHIVE_ACCEPT,
  SOURCE_ACCEPT,
  type BulkItem,
} from "@/lib/bulk-upload";
import { CollectionNodeRead } from "@/types";
import { ApiError } from "@/lib/errors";
import { useRouter } from "@/lib/navigation";
import {
  runModelUpload,
  runBulkModelUpload,
  type ModelUploadFiles,
} from "@/lib/model-upload-workflow";
import type { UploadedModelResult } from "@/lib/model-upload-result";
import { startArchiveTransfer } from "@/lib/archive-upload";

// `webkitdirectory` enables folder selection on a file input but isn't in the
// standard DOM typings — augment so the JSX attribute typechecks.
declare module "react" {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface InputHTMLAttributes<T> {
    webkitdirectory?: string;
  }
}

export type UploadMode = "files" | "bulk" | "url" | "zip";

/**
 * The open-time props the modal seeds its form from: files or bulk items handed
 * over by a drag-and-drop on the vault grid, plus the tab they belong on.
 */
interface UploadSeed {
  preloadFiles: File[] | null;
  preloadItems: BulkItem[] | null;
  initialMode: UploadMode | null;
}

/** Two preloads seed the same thing when they are the same list, or both empty. */
function samePreload<T>(applied: T[] | null, next: T[] | null): boolean {
  return applied === next || ((applied?.length ?? 0) === 0 && (next?.length ?? 0) === 0);
}

function sameSeed(applied: UploadSeed | null, next: UploadSeed): boolean {
  return (
    applied !== null &&
    applied.initialMode === next.initialMode &&
    samePreload(applied.preloadFiles, next.preloadFiles) &&
    samePreload(applied.preloadItems, next.preloadItems)
  );
}

// Whether a filename matches a comma-separated `accept` extension list
// (e.g. ".stl,.3mf,.obj"). Used to validate drag-and-drop drops, which —
// unlike a native file input — don't enforce the `accept` attribute.
function acceptsFile(accept: string, name: string): boolean {
  const exts = accept.split(",").map((e) => e.trim().toLowerCase());
  return exts.includes(extensionOf(name));
}

function stemName(filename: string): string {
  return filename.replace(/\.[^/.]+$/, "");
}

export function UploadModal({
  open,
  onClose,
  onUploaded,
  defaultCollection,
  preloadFiles,
  preloadItems,
  initialMode,
  onboarding = false,
  onTaskStarted,
}: {
  open: boolean;
  onClose: () => void;
  onUploaded: (result?: UploadedModelResult) => Promise<void>;
  defaultCollection?: string | null;
  preloadFiles?: File[] | null;
  preloadItems?: BulkItem[] | null;
  initialMode?: UploadMode;
  onboarding?: boolean;
  onTaskStarted?: (taskId: string) => void;
}) {
  const sourceUrlId = useId();
  useUiLocale();
  const { t } = useI18n();
  const { createTag } = useTaxonomyCommands();
  const router = useRouter();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const meshRef = useRef<HTMLInputElement>(null);
  const gcodeRef = useRef<HTMLInputElement>(null);
  const [meshFile, setMeshFile] = useState<File | null>(null);
  const [gcodeFile, setGcodeFile] = useState<File | null>(null);
  const [mode, setMode] = useState<UploadMode>("files");
  const modeMotion = useContentMotion(mode, { enabled: open, scroll: "self" });
  // Bulk mode: each mesh becomes its own model, queued as an independent
  // ingest task. No mesh+G-code linking (that stays on the "Files" tab).
  // Picking/dropping a folder mirrors its subfolders into nested collections.
  const [bulkFiles, setBulkFiles] = useState<BulkItem[]>([]);
  const bulkRef = useRef<HTMLInputElement>(null);
  const bulkFolderRef = useRef<HTMLInputElement>(null);
  const [urlValue, setUrlValue] = useState("");
  const [zipFile, setZipFile] = useState<File | null>(null);
  const zipRef = useRef<HTMLInputElement>(null);
  const [modelName, setModelName] = useState("");
  const [tagInput, setTagInput] = useState("");
  const [tagSuggestionsOpen, setTagSuggestionsOpen] = useState(false);
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [pendingTagCount, setPendingTagCount] = useState(0);
  const tagScope = useRef(0);
  const [submitting, setSubmitting] = useState(false);
  // Mounted-source write-back: when library sources are enabled, new uploads can target a
  // writable mounted source.
  // instead of vault storage. Empty string = vault.
  const config = useQuery({
    ...vaultConfigOptions(),
    enabled: open && !!user?.is_superuser,
    staleTime: 0,
  });
  const sources = useQuery({
    ...librarySourcesOptions(),
    enabled:
      open && !!user?.is_superuser && !!config.data?.external_libraries_enabled && !config.isError,
    staleTime: 0,
  });
  const libraries =
    user?.is_superuser &&
    config.data?.external_libraries_enabled &&
    !config.isError &&
    !sources.isError &&
    sources.data?.kind === "enabled"
      ? sources.data.items.filter(
          (library) =>
            library.enabled &&
            library.binding_state === "bound" &&
            (library.source_kind ?? "mounted") === "mounted",
        )
      : [];
  const [targetLibraryId, setTargetLibraryId] = useState<number | "">("");
  const sourceSelected = targetLibraryId !== "";
  const selectedAvailable = libraries.some((library) => library.id === targetLibraryId);
  const destinationBlocked =
    sourceSelected && (!selectedAvailable || config.isFetching || sources.isFetching);
  const sourceMode = mode === "files" || mode === "bulk";
  const catalogFailed =
    !!user?.is_superuser &&
    (config.isError || (!!config.data?.external_libraries_enabled && sources.isError));
  // Shared taxonomy lists from the TanStack Query cache (deduped with the grid
  // and detail views; refetched after any create/delete).
  const { data: tags = [] } = useTags();
  const [catOpen, setCatOpen] = useState(false);
  // A non-administrator's upload lands in the first folder they may write to,
  // found without listing the library (#295).
  const writableProbe = useCollectionSearch("", "edit", {
    enabled: open && !user?.is_superuser && !defaultCollection,
  });
  const firstWritable = writableProbe.data?.pages[0]?.items[0] ?? null;

  function sortIntoSlots(files: File[]) {
    for (const f of files) {
      if (isSourceFile(f.name)) setMeshFile(f);
      else if (isGcodeFile(f.name)) setGcodeFile(f);
    }
  }

  function applySeed(seed: UploadSeed) {
    if (seed.initialMode) setMode(seed.initialMode);
    if (!seed.preloadFiles?.length && !seed.preloadItems?.length) return;
    if (seed.initialMode === "bulk") {
      setBulkFiles(
        seed.preloadItems?.length ? seed.preloadItems : fileListToItems(seed.preloadFiles ?? []),
      );
    } else if (seed.initialMode === "zip") {
      setZipFile(seed.preloadFiles?.[0] ?? null);
    } else {
      setMeshFile(null);
      setGcodeFile(null);
      setModelName("");
      sortIntoSlots(seed.preloadFiles ?? []);
    }
  }

  // Where an upload lands unless the user picks another collection. Derived
  // during render instead of pushed into state by an effect, so it is right on
  // the first frame and still right when the writable-collection list arrives
  // after the modal is already open — without overwriting a pick made since.
  const suggestedCollection =
    defaultCollection || (!user?.is_superuser && firstWritable ? firstWritable.path : "");
  // A pick is the collection itself, "none" for no collection, or null for no pick yet.
  const [pickedCollection, setPickedCollection] = useState<CollectionNodeRead | "none" | null>(
    null,
  );
  const collectionPath =
    pickedCollection === null
      ? suggestedCollection
      : pickedCollection === "none"
        ? ""
        : pickedCollection.path;
  // A suggested path still needs its names and id; a picked one came with them.
  const suggestedLookup = useCollectionLookup(
    open && pickedCollection === null && suggestedCollection !== "" ? suggestedCollection : null,
  );
  const chosenCollection: CollectionNodeRead | null =
    pickedCollection !== null && pickedCollection !== "none"
      ? pickedCollection
      : suggestedLookup.data?.collection.path === collectionPath
        ? suggestedLookup.data.collection
        : null;
  const selectedCollectionLabel = chosenCollection?.display_path ?? "";

  // Seeding the form from the props above is an adjustment to changed props,
  // not synchronization with an external system, so it happens during render:
  // the modal's first painted frame already shows the dropped files. Closing
  // forgets the seed, so the next open re-applies it.
  const [seeded, setSeeded] = useState<UploadSeed | null>(null);
  const seed: UploadSeed = {
    preloadFiles: preloadFiles ?? null,
    preloadItems: preloadItems ?? null,
    initialMode: initialMode ?? null,
  };
  if (!open && seeded !== null) setSeeded(null);
  if (open && !sameSeed(seeded, seed)) {
    setSeeded(seed);
    setPickedCollection(null);
    applySeed(seed);
  }

  const filteredTags = useMemo(() => {
    const q = tagInput.toLowerCase().trim();
    return tags.filter(
      (t) => !selectedTags.includes(t.slug) && (q === "" || t.name.toLowerCase().includes(q)),
    );
  }, [tags, tagInput, selectedTags]);

  const canCreateNewTag =
    tagInput.trim().length > 0 &&
    !tags.find((t) => t.name.toLowerCase() === tagInput.trim().toLowerCase());

  const shownTags = filteredTags.slice(0, 6);
  const tagItems = [...shownTags, ...(canCreateNewTag ? [tagInput.trim()] : [])];
  const tagNav = useComboboxNav(tagInput && tagSuggestionsOpen ? tagItems.length : 0, {
    onClose: () => setTagSuggestionsOpen(false),
    onSelect: (i) => {
      if (i < shownTags.length) {
        toggleTag(shownTags[i].slug);
      } else {
        doCreateTag(tagInput);
      }
      setTagInput("");
    },
    onCommitInput: () => {
      if (tagInput.trim()) {
        doCreateTag(tagInput);
        setTagInput("");
      }
    },
  });

  function autoName(f: File) {
    if (!modelName) setModelName(stemName(f.name));
  }

  // Merge newly picked/dropped meshes into the bulk queue (drops non-mesh
  // files and duplicates), then surface a notice for anything skipped.
  function addBulkItems(items: BulkItem[]) {
    setBulkFiles((prev) => mergeBulkItems(prev, items).items);
    const skipped = items.length - items.filter((it) => isSourceFile(it.file.name)).length;
    if (skipped > 0) {
      toast.warning(
        uiText("Some files skipped"),
        uiText("uploads.ignored", {
          value1: String(skipped),
          value3: String(SOURCE_ACCEPT),
          count: Number(skipped),
        }),
      );
    }
  }

  function reset() {
    tagScope.current += 1;
    setPendingTagCount(0);
    setMeshFile(null);
    setGcodeFile(null);
    setMode("files");
    setBulkFiles([]);
    setUrlValue("");
    setZipFile(null);
    setModelName("");
    setPickedCollection(null);
    setSelectedTags([]);
    setTagInput("");
    setTargetLibraryId("");
    setSubmitting(false);
  }

  function close() {
    reset();
    onClose();
  }

  function collectionGate(): boolean {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return false;
    }
    if (!user?.is_superuser && !collectionPath) {
      toast.warning(uiText("Collection required"), uiText("Choose a collection you can edit."));
      return false;
    }
    return true;
  }

  async function runUrlImport() {
    if (!auth.isAuthenticated || submitting) {
      if (!auth.isAuthenticated) auth.showAuthRequiredToast();
      return;
    }
    setSubmitting(true);
    try {
      const item = await capturePendingImport({
        url: urlValue.trim(),
        collection_id: chosenCollection?.id ?? null,
        tags: selectedTags,
      });
      toast.success(uiText("Captured URL. Review it before importing."));
      close();
      router.push(`/inbox/${item.id}`);
    } catch (err) {
      toast.error(err);
      setSubmitting(false);
    }
  }

  function doInspectZip() {
    if (!collectionGate() || submitting || !zipFile) return;
    const file = zipFile;
    const taskId = createTask({
      title: uiMessage("Upload {value1}", { value1: String(file.name) }),
      detail: uiText("Transferring file"),
      status: "running",
      progress: 0,
      archiveUploading: true,
      archiveSizeBytes: file.size,
      archiveCollection: collectionPath || null,
      archiveTags: [...selectedTags],
    });
    onTaskStarted?.(taskId);
    void startArchiveTransfer(taskId, file);
    close();
  }

  function doSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || pendingTagCount > 0 || (sourceMode && destinationBlocked)) return;
    if (mode === "url") {
      if (!urlValue.trim()) return;
      void runUrlImport();
      return;
    }
    if (mode === "zip") {
      if (!zipFile) return;
      doInspectZip();
      return;
    }
    if (mode === "bulk") {
      if (bulkFiles.length === 0) return;
      if (!collectionGate()) return;
      void runBulkModelUpload(
        {
          files: bulkFiles,
          collection: collectionPath,
          tagsForUpload: [...selectedTags],
          libraryId: targetLibraryId,
        },
        onUploaded,
      );
      toast.success(
        uiText("uploads.queued", {
          value1: String(bulkFiles.length),
          count: Number(bulkFiles.length),
        }),
      );
      reset();
      onClose();
      return;
    }
    const files: ModelUploadFiles | null = meshFile
      ? { mesh: meshFile, gcode: gcodeFile }
      : gcodeFile
        ? { mesh: null, gcode: gcodeFile }
        : null;
    if (!files) return;
    if (!collectionGate()) return;
    const taskId = createTask({
      title: uiMessage("Upload {value1}", {
        value1: String(modelName || meshFile?.name || gcodeFile?.name || "model"),
      }),
      detail: uiMessage("Preparing upload"),
      status: "running",
      progress: 5,
      expectedJobCount: meshFile && gcodeFile ? 2 : 1,
    });
    setSubmitting(true);
    onTaskStarted?.(taskId);
    void runModelUpload(
      {
        taskId,
        ...files,
        name: modelName,
        collection: collectionPath,
        tagsForUpload: [...selectedTags],
        libraryId: targetLibraryId,
      },
      onUploaded,
    );
    reset();
    onClose();
  }

  async function doCreateTag(name: string) {
    const trimmed = name.trim();
    if (!trimmed) return;
    const existing = tags.find((t) => t.name.toLowerCase() === trimmed.toLowerCase());
    if (existing) {
      if (!selectedTags.includes(existing.slug)) toggleTag(existing.slug);
      return;
    }
    const scope = tagScope.current;
    setPendingTagCount((count) => count + 1);
    try {
      const t = await createTag({ name: trimmed });
      if (scope !== tagScope.current) return;
      // createTag invalidates the query cache → useTags() refetches the new
      // tag; we just select it here.
      setSelectedTags((p) => [...p, t.slug]);
    } catch (err) {
      if (scope !== tagScope.current) return;
      // 401 is surfaced by AuthBanner; duplicate slug is harmless.
      if (err instanceof ApiError && err.isAuthError) return;
      toast.error(err);
    } finally {
      if (scope === tagScope.current) setPendingTagCount((count) => count - 1);
    }
  }

  function toggleTag(slug: string) {
    setSelectedTags((p) => (p.includes(slug) ? p.filter((s) => s !== slug) : [...p, slug]));
  }

  return (
    <ModalShell
      open={open}
      onClose={close}
      labelledBy="upload-modal-title"
      className={`upload-dialog border border-border bg-card shadow-lg ${onboarding ? "upload-dialog-onboarding" : "upload-dialog-standard"}`}
    >
      <>
        <div className="upload-dialog-heading flex items-start justify-between gap-4 border-b border-border px-6 py-6">
          <div>
            <h3
              id="upload-modal-title"
              className={
                onboarding
                  ? "text-xl font-semibold tracking-tight text-on-surface"
                  : "text-lg font-semibold text-foreground"
              }
            >
              {onboarding ? t("setup.firstUpload") : uiText("Upload model")}
            </h3>
            <p className="mt-1 text-sm text-muted-foreground">
              {onboarding
                ? t("setup.firstUploadHelp")
                : uiText("Add a 3D Model, an existing G-code file, or both together.")}
            </p>
          </div>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={close}
            aria-label={uiText("Close")}
            className="-mr-2 -mt-2 shrink-0 text-muted-foreground"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>

        {!auth.isAuthenticated && (
          <div className="mx-6 mt-4 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-200 font-mono">
            {uiText("Sign in before uploading.")}{" "}
            <a href="/login" className="underline">
              {uiText("Open login")}
            </a>
            .
          </div>
        )}

        <form onSubmit={doSubmit} className="upload-dialog-form">
          {/* Mode tabs */}
          {!onboarding && (
            <TabBar
              tabs={(
                [
                  ["files", "uploads.mode.files"],
                  ["bulk", "uploads.mode.bulk"],
                  ["url", "uploads.mode.url"],
                  ["zip", "uploads.mode.zip"],
                ] as const
              ).map(([key, label]) => ({ key, label: t(label) }))}
              active={mode}
              onChange={(next) => {
                setCatOpen(false);
                setTagSuggestionsOpen(false);
                setMode(next);
              }}
              showIndicator={false}
              className="upload-mode-tabs"
              tabClassName="upload-mode-tab"
            />
          )}

          <div
            ref={modeMotion}
            className="upload-mode-content space-y-5"
            role={onboarding ? undefined : "tabpanel"}
            aria-label={onboarding ? undefined : t(`uploads.mode.${mode}`)}
          >
            {/* Input area */}
            {onboarding ? (
              <div className="space-y-3">
                <label htmlFor="setup-upload-file" className="block text-sm font-medium">
                  {t("setup.chooseFile")}
                </label>
                <label
                  htmlFor="setup-upload-file"
                  className="flex min-h-32 cursor-action flex-col items-center justify-center gap-3 rounded-md border border-dashed border-outline-variant bg-surface-container-low p-5 text-center hover:bg-surface-container focus-within:ring-2 focus-within:ring-ring"
                >
                  <Upload className="h-6 w-6 text-muted-foreground" aria-hidden />
                  <span className="max-w-full break-all text-sm font-medium">
                    {meshFile?.name || gcodeFile?.name || t("setup.chooseFileAction")}
                  </span>
                  <input
                    id="setup-upload-file"
                    type="file"
                    accept={`${SOURCE_ACCEPT},${GCODE_ACCEPT}`}
                    className="sr-only"
                    aria-label={t("setup.chooseFile")}
                    onChange={(event) => {
                      const file = event.target.files?.[0] ?? null;
                      setMeshFile(file && isSourceFile(file.name) ? file : null);
                      setGcodeFile(file && isGcodeFile(file.name) ? file : null);
                      if (file) autoName(file);
                    }}
                    aria-describedby="setup-upload-formats"
                  />
                </label>
                <p
                  id="setup-upload-formats"
                  className="text-xs leading-relaxed text-muted-foreground"
                >
                  {t("setup.fileFormats")}
                </p>
              </div>
            ) : mode === "files" ? (
              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                <FileSlot
                  label={uiText("3D model / resin slice")}
                  accept={SOURCE_ACCEPT}
                  file={meshFile}
                  setFile={(f) => {
                    setMeshFile(f);
                    if (f) autoName(f);
                  }}
                  placeholder={uiText(
                    ".stl .3mf .obj .glb .gltf .ply .3dm .fbx .step .dxf .ctb .goo",
                  )}
                  inputRef={meshRef}
                />
                <FileSlot
                  label="G-code"
                  accept={GCODE_ACCEPT}
                  file={gcodeFile}
                  setFile={(f) => {
                    setGcodeFile(f);
                    if (f) autoName(f);
                  }}
                  placeholder={uiText(".gcode .g .gco .bgcode")}
                  inputRef={gcodeRef}
                />
              </div>
            ) : mode === "bulk" ? (
              <BulkFiles
                items={bulkFiles}
                fileInputRef={bulkRef}
                folderInputRef={bulkFolderRef}
                onAddItems={addBulkItems}
                onRemove={(idx) => setBulkFiles((prev) => prev.filter((_, i) => i !== idx))}
                onClear={() => setBulkFiles([])}
              />
            ) : mode === "url" ? (
              <div>
                <label htmlFor={sourceUrlId} className="mb-2 block text-sm text-foreground">
                  {uiText("Source URL")}
                </label>
                <input
                  id={sourceUrlId}
                  type="url"
                  inputMode="url"
                  value={urlValue}
                  onChange={(e) => setUrlValue(e.target.value)}
                  className="h-10 w-full border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                  placeholder={uiText("Model page, collection, or direct .stl/.zip link")}
                />
                <p className="mt-1.5 max-w-prose text-xs leading-5 text-muted-foreground">
                  {uiText(
                    "Create a durable capture, then review its files and confirm or change the collection and tags.",
                  )}
                </p>
              </div>
            ) : (
              <FileSlot
                label={uiText("Archive")}
                large
                accept={ARCHIVE_ACCEPT}
                file={zipFile}
                setFile={setZipFile}
                placeholder={uiText(".zip .7z .rar .tar .tgz")}
                inputRef={zipRef}
              />
            )}

            {!onboarding && (
              <h4 className="text-sm font-semibold">{uiText("Library destination")}</h4>
            )}
            <div className={onboarding ? "space-y-5" : "grid grid-cols-1 gap-5 sm:grid-cols-2"}>
              {/* Model name (single-file uploads only; URL imports take their
                  name from the downloaded file/page) */}
              {mode === "files" && (
                <div>
                  <label
                    htmlFor="upload-model-name"
                    className="block text-sm font-medium text-on-surface mb-2"
                  >
                    {uiText("Model name")}
                  </label>
                  <input
                    id="upload-model-name"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    className="h-10 w-full border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                    placeholder={uiText("e.g. Bracket v2")}
                  />
                  <p className="mt-2 text-xs text-muted-foreground">
                    {uiText("Optional. Uses the selected filename when left blank.")}
                  </p>
                </div>
              )}

              {/* First uploads need no collection or tags; these remain in the library upload dialog. */}
              {!onboarding && (
                <>
                  {/* Collection */}
                  <div>
                    <label className="mb-2 block text-sm text-foreground">
                      {uiText("Collection")}
                    </label>
                    <DropdownMenu
                      open={catOpen}
                      onOpenChange={setCatOpen}
                      portal
                      align="start"
                      role="dialog"
                      contentClassName="w-96 bg-surface-container-lowest border border-outline-variant rounded shadow-lg"
                      trigger={
                        <button
                          type="button"
                          data-menu-trigger
                          onClick={() => setCatOpen((v) => !v)}
                          aria-haspopup="dialog"
                          aria-expanded={catOpen}
                          title={selectedCollectionLabel || undefined}
                          className="flex h-10 w-full min-w-0 items-center gap-2 border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                        >
                          <span
                            className={`min-w-0 flex-1 truncate text-left ${collectionPath ? "" : "text-on-surface-variant/60"}`}
                          >
                            {selectedCollectionLabel ||
                              (user?.is_superuser ? uiText("None") : uiText("Choose collection"))}
                          </span>
                          <ChevronDown className="h-4 w-4 shrink-0 text-on-surface-variant" />
                        </button>
                      }
                    >
                      <div className="p-2">
                        <CollectionPicker
                          minRole="edit"
                          selectedPath={collectionPath}
                          onSelect={(collection) => {
                            setPickedCollection(collection ?? "none");
                            setCatOpen(false);
                          }}
                          noneLabel={user?.is_superuser ? uiText("None") : undefined}
                          emptyLabel={uiText("No editable collections.")}
                        />
                      </div>
                    </DropdownMenu>
                  </div>

                  {/* Destination (mounted-source write-back) — only when an eligible source exists */}
                  {sourceMode && (
                    <div>
                      <label className="mb-2 block text-sm text-foreground">
                        {uiText("Store in")}
                      </label>
                      <select
                        aria-label={uiText("Store in")}
                        value={targetLibraryId}
                        onChange={(e) =>
                          setTargetLibraryId(e.target.value === "" ? "" : Number(e.target.value))
                        }
                        className="h-10 w-full border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                      >
                        <option value="">{uiText("Vault storage")}</option>
                        {sourceSelected && !selectedAvailable && (
                          <option value={targetLibraryId} disabled>
                            {uiText("Selected source unavailable")}
                          </option>
                        )}
                        {libraries.map((lib) => (
                          <option key={lib.id} value={lib.id}>
                            {uiText("{value1} (library source)", {
                              value1: String(lib.name ?? ""),
                            })}
                          </option>
                        ))}
                      </select>
                      {(libraries.length > 0 || sourceSelected) && (
                        <p className="mt-2 text-xs text-muted-foreground">
                          {uiText(
                            "Eligible mounted sources use create-only write-back. Revisions to a linked model follow that source automatically.",
                          )}
                        </p>
                      )}
                    </div>
                  )}

                  {/* Tags */}
                  <div className={mode === "files" ? "min-w-0" : "min-w-0 sm:col-span-2"}>
                    <label className="mb-2 block text-sm text-foreground">{uiText("Tags")}</label>
                    <div className="flex items-start gap-2">
                      <DropdownMenu
                        portal
                        role="dialog"
                        align="start"
                        aria-label={uiText("Tags")}
                        open={tagSuggestionsOpen && !!tagInput && tagItems.length > 0}
                        onOpenChange={setTagSuggestionsOpen}
                        className="min-w-0 flex-1"
                        contentClassName="w-80 max-h-40 overflow-y-auto border border-border bg-popover py-1 shadow-lg"
                        trigger={
                          <input
                            value={tagInput}
                            data-menu-trigger
                            onFocus={() => setTagSuggestionsOpen(true)}
                            onChange={(e) => {
                              setTagInput(e.target.value);
                              setTagSuggestionsOpen(true);
                              tagNav.setActiveIndex(-1);
                            }}
                            {...tagNav.inputProps}
                            onKeyDown={(e) => {
                              tagNav.inputProps.onKeyDown(e);
                              if (e.defaultPrevented) return;
                              if (e.key === "Backspace" && !tagInput && selectedTags.length) {
                                setSelectedTags((p) => p.slice(0, -1));
                              }
                            }}
                            placeholder={uiText("Search or create — press Enter")}
                            className="h-10 w-full border border-input bg-card px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                          />
                        }
                      >
                        <div
                          id={tagNav.listboxId}
                          role="listbox"
                          className="text-popover-foreground"
                        >
                          {shownTags.map((t, i) => (
                            <button
                              key={t.id}
                              id={tagNav.optionId(i)}
                              role="option"
                              aria-selected={i === tagNav.activeIndex}
                              type="button"
                              onClick={() => {
                                toggleTag(t.slug);
                                setTagInput("");
                              }}
                              className={`w-full text-left px-3 py-1.5 font-mono text-xs text-on-surface-variant hover:bg-surface-container-low flex justify-between ${i === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
                            >
                              <span>{t.name}</span>
                              <span className="opacity-50">({t.model_count})</span>
                            </button>
                          ))}
                          {canCreateNewTag && (
                            <button
                              type="button"
                              id={tagNav.optionId(shownTags.length)}
                              role="option"
                              aria-selected={shownTags.length === tagNav.activeIndex}
                              onClick={() => {
                                doCreateTag(tagInput);
                                setTagInput("");
                              }}
                              className={`w-full text-left px-3 py-1.5 font-mono text-xs text-primary hover:bg-surface-container-low flex items-center gap-2 ${shownTags.length === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
                            >
                              <Plus className="h-3 w-3" />
                              {uiText(' Create "')}
                              {tagInput.trim()}
                              {uiText('"')}
                            </button>
                          )}
                        </div>
                      </DropdownMenu>
                      <Button
                        type="button"
                        variant="outline"
                        className="h-10 shrink-0"
                        disabled={!tagInput.trim() || pendingTagCount > 0}
                        onClick={() => {
                          const found = tags.find(
                            (tag) =>
                              tag.name.toLocaleLowerCase() ===
                                tagInput.trim().toLocaleLowerCase() || tag.slug === tagInput.trim(),
                          );
                          if (found) {
                            if (!selectedTags.includes(found.slug)) toggleTag(found.slug);
                            setTagInput("");
                          } else if (canCreateNewTag) {
                            void doCreateTag(tagInput);
                            setTagInput("");
                          }
                        }}
                      >
                        {uiText("Add tag")}
                      </Button>
                    </div>
                    {selectedTags.length > 0 && (
                      <div className="flex flex-wrap gap-1.5 mt-2">
                        {selectedTags.map((slug) => {
                          const t = tags.find((x) => x.slug === slug);
                          return (
                            <span
                              key={slug}
                              className="inline-flex items-center gap-1 bg-secondary-container text-on-secondary-container pl-2 pr-1 py-0.5 rounded font-mono text-3xs uppercase tracking-wider"
                            >
                              {t?.name || slug}
                              <button
                                type="button"
                                onClick={() => toggleTag(slug)}
                                aria-label={uiText("Remove {value1}", {
                                  value1: String(t?.name || slug),
                                })}
                                className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-on-secondary-container/10"
                              >
                                <X className="h-3 w-3" />
                              </button>
                            </span>
                          );
                        })}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
            {sourceMode && (catalogFailed || (sourceSelected && !selectedAvailable)) && (
              <div role="alert" className="space-y-2 text-sm text-destructive">
                {catalogFailed && <p>{uiText("Could not load upload destinations.")}</p>}
                {sourceSelected && !selectedAvailable && (
                  <p>{uiText("Choose an available destination before uploading.")}</p>
                )}
                {catalogFailed && (
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => {
                      if (config.isError) void config.refetch();
                      else void sources.refetch();
                    }}
                  >
                    {uiText("Retry")}
                  </Button>
                )}
              </div>
            )}
            {!onboarding && (
              <p className="pb-1 text-xs text-muted-foreground">
                {uiText(
                  "PrintStash stores existing files. It does not slice Models or verify G-code safety.",
                )}
              </p>
            )}
          </div>
          <div className="upload-dialog-actions flex justify-end gap-2 border-t border-border bg-muted/30 px-6 py-4">
            <Button type="button" variant="outline" onClick={close} size="sm">
              {uiText("Cancel")}
            </Button>
            <Button
              type="submit"
              aria-busy={pendingTagCount > 0}
              disabled={
                submitting ||
                pendingTagCount > 0 ||
                (sourceMode && destinationBlocked) ||
                (!user?.is_superuser && !collectionPath) ||
                // A destination that has not resolved yet would land at the root.
                (collectionPath !== "" && chosenCollection === null) ||
                (mode === "files"
                  ? !meshFile && !gcodeFile
                  : mode === "bulk"
                    ? bulkFiles.length === 0
                    : mode === "url"
                      ? !urlValue.trim()
                      : !zipFile)
              }
              size="sm"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {mode === "zip" ? uiText("Preparing archive…") : uiText("Working…")}
                </>
              ) : mode === "bulk" ? (
                bulkFiles.length > 0 ? (
                  uiText("counts.uploadModels", { count: bulkFiles.length })
                ) : (
                  uiText("Upload to vault")
                )
              ) : mode === "url" ? (
                uiText("Capture for review")
              ) : mode === "zip" ? (
                uiText("Prepare archive")
              ) : onboarding ? (
                t("setup.addModel")
              ) : (
                uiText("Upload to vault")
              )}
            </Button>
          </div>
        </form>
      </>
    </ModalShell>
  );
}

function FileSlot({
  label,
  accept,
  file,
  setFile,
  placeholder,
  inputRef,
  large = false,
}: {
  label: string;
  large?: boolean;
  accept: string;
  file: File | null;
  setFile: (f: File | null) => void;
  placeholder: string;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  useUiLocale();
  const [dragActive, setDragActive] = useState(false);
  // Count drag enter/leave so crossing a child element doesn't flicker the
  // highlight off — only a real leave (depth back to 0) clears it.
  const dragDepth = useRef(0);
  return (
    <div>
      <span className="mb-2 block text-sm text-foreground">{label}</span>
      <div
        onClick={() => {
          if (!file) inputRef.current?.click();
        }}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setDragActive(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={() => {
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragActive(false);
          }
        }}
        onDrop={(e) => {
          e.preventDefault();
          dragDepth.current = 0;
          setDragActive(false);
          const dropped = e.dataTransfer.files?.[0];
          if (!dropped) return;
          if (!acceptsFile(accept, dropped.name)) {
            toast.warning(
              uiText("Wrong file type for {value1}", { value1: String(label) }),
              uiText("Drop a {value1} file here.", { value1: String(accept) }),
            );
            return;
          }
          setFile(dropped);
        }}
        className={`flex ${large ? "min-h-[150px] flex-col items-center justify-center text-center" : "min-h-[104px] items-start justify-between"} gap-3 border border-dashed px-4 py-3 transition-colors cursor-action ${
          file
            ? "border-primary bg-primary/5"
            : dragActive
              ? "border-primary bg-primary/10"
              : "border-input bg-muted/20 hover:border-ring"
        }`}
      >
        {file ? (
          <div className="flex items-center gap-2 min-w-0 flex-1">
            <FileIcon className="h-4 w-4 text-primary flex-shrink-0" />
            <span className="text-xs font-medium text-on-surface truncate">{file.name}</span>
            <span className="font-mono text-3xs text-on-surface-variant flex-shrink-0">
              {formatBytes(file.size)}
            </span>
            <button
              type="button"
              onClick={(ev) => {
                ev.stopPropagation();
                setFile(null);
              }}
              aria-label={uiText("Remove {value1}", { value1: String(label) })}
              className="h-5 w-5 rounded hover:bg-surface-container flex items-center justify-center text-on-surface-variant flex-shrink-0"
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ) : (
          <>
            <Upload className="mt-2 h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
            <div className={large ? "flex min-w-0 flex-col items-center" : "min-w-0 flex-1"}>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  aria-label={uiText("Choose file for {value1}", { value1: label })}
                  onClick={(event) => {
                    event.stopPropagation();
                    inputRef.current?.click();
                  }}
                >
                  {uiText("Choose file")}
                </Button>
                <span className="text-xs text-muted-foreground">
                  {uiText("or drop files here")}
                </span>
              </div>
              <p className="mt-2 break-words text-xs text-muted-foreground">{placeholder}</p>
            </div>
          </>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={accept}
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="hidden"
        />
      </div>
      <p className="mt-2 text-xs text-muted-foreground">
        {file ? file.name : uiText("No files selected")}
      </p>
    </div>
  );
}

// Exported for unit tests; not used outside this module.
export function BulkFiles({
  items,
  fileInputRef,
  folderInputRef,
  onAddItems,
  onRemove,
  onClear,
}: {
  items: BulkItem[];
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  folderInputRef: React.RefObject<HTMLInputElement | null>;
  onAddItems: (items: BulkItem[]) => void;
  onRemove: (index: number) => void;
  onClear: () => void;
}) {
  useUiLocale();
  const [dragActive, setDragActive] = useState(false);
  // See FileSlot: count enter/leave depth so dragging over child elements
  // (icon, hint text, the "select a folder" button) doesn't flicker the
  // highlight off.
  const dragDepth = useRef(0);
  const totalBytes = items.reduce((sum, it) => sum + it.file.size, 0);
  const folderCount = new Set(items.map((it) => it.relPath).filter(Boolean)).size;

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
    const dt = e.dataTransfer;
    // Entries must be pulled out synchronously — the DataTransfer is emptied
    // once this handler returns; the async folder walk happens afterwards.
    const entries = entriesFromDataTransfer(dt.items);
    if (entries.length > 0) {
      walkEntries(entries)
        .then(onAddItems)
        .catch((err) =>
          toast.error(err instanceof Error ? err : new Error("Couldn't read the dropped folder")),
        );
    } else if (dt.files?.length) {
      // Browsers without the entries API still give a flat FileList.
      onAddItems(fileListToItems(dt.files));
    }
  }

  return (
    <div>
      <p className="mb-2 text-sm text-foreground">{uiText("Model files")}</p>
      <div
        onClick={() => fileInputRef.current?.click()}
        onDragEnter={(e) => {
          e.preventDefault();
          dragDepth.current += 1;
          setDragActive(true);
        }}
        onDragOver={(e) => {
          e.preventDefault();
          e.dataTransfer.dropEffect = "copy";
        }}
        onDragLeave={() => {
          dragDepth.current -= 1;
          if (dragDepth.current <= 0) {
            dragDepth.current = 0;
            setDragActive(false);
          }
        }}
        onDrop={handleDrop}
        className={`flex min-h-[150px] flex-col items-center justify-center gap-3 border border-dashed bg-muted/20 px-4 py-5 text-center transition-colors cursor-action ${
          dragActive
            ? "border-primary bg-primary/10"
            : "border-outline-variant hover:border-outline"
        }`}
      >
        <Upload className="h-5 w-5 text-muted-foreground" aria-hidden />
        <div className="flex flex-wrap items-center justify-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={(event) => {
              event.stopPropagation();
              fileInputRef.current?.click();
            }}
          >
            {uiText("Choose files")}
          </Button>
          <span className="text-xs text-muted-foreground">{uiText("or drop files here")}</span>
        </div>
        <p className="text-xs text-muted-foreground">
          {uiText("Choose Model files or drop a folder. G-code pairing is available on Files.")}
        </p>
      </div>
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => folderInputRef.current?.click()}
        >
          {uiText("Choose folder")}
        </Button>
        <span className="text-xs text-muted-foreground">
          {items.length ? (
            <>
              {uiText("counts.files", { count: items.length })}
              {folderCount > 0
                ? uiText("folders.count", { value1: String(folderCount), count: folderCount })
                : ""}{" "}
              · {formatBytes(totalBytes)}
            </>
          ) : (
            uiText("No files selected")
          )}
        </span>
        <Button type="button" variant="ghost" size="sm" onClick={onClear} disabled={!items.length}>
          {uiText("Clear all")}
        </Button>
      </div>
      <p className="mt-6 text-xs text-muted-foreground">
        {uiText("Each Model file creates a separate Model. Subfolders become nested Collections.")}
      </p>
      {/* Outside the drop zone: a programmatic click on either input bubbles, and the
          zone's own onClick would open the file picker over the folder picker. */}
      <input
        ref={fileInputRef}
        type="file"
        accept={SOURCE_ACCEPT}
        multiple
        onChange={(e) => {
          if (e.target.files?.length) onAddItems(fileListToItems(e.target.files));
          // Allow re-picking the same files after a removal.
          e.target.value = "";
        }}
        className="hidden"
      />
      <input
        ref={folderInputRef}
        type="file"
        webkitdirectory=""
        multiple
        onChange={(e) => {
          if (e.target.files?.length) onAddItems(fileListToItems(e.target.files));
          e.target.value = "";
        }}
        className="hidden"
      />
      {items.length > 0 && (
        <>
          <div className="rounded border border-outline-variant divide-y divide-outline-variant max-h-56 overflow-y-auto">
            {items.map((it, idx) => (
              <div
                key={`${it.relPath}/${it.file.name}:${it.file.size}:${idx}`}
                className="flex items-center gap-2 px-3 py-2"
              >
                <FileIcon className="h-4 w-4 text-primary flex-shrink-0" />
                <span className="min-w-0 flex-1 truncate text-xs text-on-surface">
                  {it.relPath && <span className="text-on-surface-variant/60">{it.relPath}/</span>}
                  {it.file.name}
                </span>
                <span className="font-mono text-3xs text-on-surface-variant flex-shrink-0">
                  {formatBytes(it.file.size)}
                </span>
                <button
                  type="button"
                  onClick={() => onRemove(idx)}
                  aria-label={uiText("Remove {value1}", { value1: String(it.file.name) })}
                  className="h-5 w-5 rounded hover:bg-surface-container flex items-center justify-center text-on-surface-variant flex-shrink-0"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
