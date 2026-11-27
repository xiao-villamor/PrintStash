"use client";

import { mergeFolderItems, prepareFolderArchive } from "@/lib/folder-archive";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useContentReveal } from "@/lib/use-content-reveal";
import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { uiMessage } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useI18n, useUiLocale } from "@/lib/i18n";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { vaultConfigOptions } from "@/lib/queries/settings-config";
import { librarySourcesOptions } from "@/lib/queries/settings-library-sources";
import { ChevronDown, File as FileIcon, Loader2, Plus, Upload, X } from "lucide-react";
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
  isArchiveFile,
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

type UploadTab = "files" | "bulk";
export type UploadMode = UploadTab | "zip";

type BulkSelection = { kind: "bulk"; items: BulkItem[] } | { kind: "zip"; file: File };
const EMPTY_BULK: BulkSelection = { kind: "bulk", items: [] };

function isBulkModel({ file }: BulkItem) {
  return isSourceFile(file.name) && !isGcodeFile(file.name);
}

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
  useUiLocale();
  const { t } = useI18n();
  const { createTag } = useTaxonomyCommands();
  const auth = useRequireAuth();
  const { user } = useAuth();
  const meshRef = useRef<HTMLInputElement>(null);
  const gcodeRef = useRef<HTMLInputElement>(null);
  const [meshFile, setMeshFile] = useState<File | null>(null);
  const [gcodeFile, setGcodeFile] = useState<File | null>(null);
  const [mode, setMode] = useState<UploadTab>("files");
  const modeMotion = useContentReveal(mode, { enabled: open && !onboarding, scroll: "parent" });
  const collectionTrigger = useRef<HTMLButtonElement>(null);
  const tagField = useRef<HTMLInputElement>(null);
  // Bulk mode: each mesh becomes its own model, queued as an independent
  // ingest task. No mesh+G-code linking (that stays on the "Files" tab).
  // Picking/dropping a folder mirrors its subfolders into nested collections.
  const [bulkSelection, setBulkSelection] = useState<BulkSelection>(EMPTY_BULK);
  const bulkFiles = bulkSelection.kind === "bulk" ? bulkSelection.items : [];
  const zipFile = bulkSelection.kind === "zip" ? bulkSelection.file : null;
  const submissionKind = mode === "files" ? "files" : bulkSelection.kind;
  const [folderReview, setFolderReview] = useState(true);
  const folderPreparation = useRef<AbortController | null>(null);
  useEffect(() => () => folderPreparation.current?.abort(), []);
  useEffect(() => {
    if (!open) folderPreparation.current?.abort();
  }, [open]);
  const hasFolder = bulkFiles.some((item) => item.relPath !== "");
  const modelFiles = bulkFiles.filter(isBulkModel);
  const bulkRef = useRef<HTMLInputElement>(null);
  const bulkFolderRef = useRef<HTMLInputElement>(null);
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
  const sourceMode = submissionKind !== "zip";
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
      if (isGcodeFile(f.name)) setGcodeFile(f);
      else if (isSourceFile(f.name)) setMeshFile(f);
    }
  }

  function applySeed(seed: UploadSeed) {
    if (seed.initialMode) setMode(seed.initialMode === "zip" ? "bulk" : seed.initialMode);
    if (seed.initialMode === "bulk") {
      const items = seed.preloadItems?.length
        ? seed.preloadItems
        : fileListToItems(seed.preloadFiles ?? []);
      setBulkSelection({ kind: "bulk", items: items.filter(isBulkModel) });
    } else if (seed.initialMode === "zip") {
      const file = seed.preloadFiles?.[0];
      setBulkSelection(file ? { kind: "zip", file } : EMPTY_BULK);
    } else if (seed.preloadFiles?.length) {
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

  const trimmedTagInput = tagInput.trim();
  const tagQuery = trimmedTagInput.toLowerCase();
  const matchingTag = tags.find(
    (tag) => tag.name.toLowerCase() === tagQuery || tag.slug === trimmedTagInput,
  );
  const filteredTags = tags.filter(
    (tag) =>
      !selectedTags.includes(tag.slug) &&
      (tag.name.toLowerCase().includes(tagQuery) || tag.slug === trimmedTagInput),
  );

  const canCreateNewTag = trimmedTagInput.length > 0 && !matchingTag;

  const shownTags = filteredTags.slice(0, 6);
  const tagItems = [...shownTags, ...(canCreateNewTag ? [tagInput.trim()] : [])];
  const tagNav = useComboboxNav(tagInput && tagSuggestionsOpen ? tagItems.length : 0, {
    onClose: () => setTagSuggestionsOpen(false),
    onSelect: (i) => {
      if (i < shownTags.length) {
        toggleTag(shownTags[i].slug);
      } else {
        doCreateTag();
      }
      setTagInput("");
    },
    onCommitInput: () => {
      if (tagInput.trim()) {
        doCreateTag();
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
    // A folder keeps its guides and images so review can retain them as Documents.
    if (hasFolder || items.some((item) => item.relPath !== "")) {
      try {
        setBulkSelection({ kind: "bulk", items: mergeFolderItems(bulkFiles, items) });
      } catch {
        toast.error(
          uiText(
            "Two selected files have the same folder path. Remove the earlier file before replacing it.",
          ),
        );
      }
      return;
    }
    const models = items.filter(isBulkModel);
    const archives = items.filter(({ file }) => isArchiveFile(file.name));
    if (models.length > 0) {
      setBulkSelection((prev) => ({
        kind: "bulk",
        items: mergeBulkItems(prev.kind === "bulk" ? prev.items : [], models).items,
      }));
      if (archives.length > 0) {
        toast.warning(uiText("Archives were skipped because Model files were selected."));
      }
    } else if (archives.length > 0) {
      setBulkSelection({ kind: "zip", file: archives[0].file });
      if (archives.length > 1) {
        toast.warning(uiText("Only the first archive was selected."));
      }
    }
    const skipped = items.length - models.length - archives.length;
    if (skipped > 0) {
      toast.warning(
        uiText("Some files skipped"),
        uiText("uploads.ignored", {
          value1: String(skipped),
          value3: bulkAccept,
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
    setBulkSelection(EMPTY_BULK);
    setFolderReview(true);
    folderPreparation.current?.abort();
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

  function doInspectZip() {
    if (!collectionGate() || submitting || !zipFile) return;
    queueArchive(zipFile);
  }

  function queueArchive(file: File) {
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

  async function reviewFolder() {
    if (!collectionGate() || submitting) return;
    const session = getSessionVersion();
    const controller = new AbortController();
    folderPreparation.current = controller;
    setSubmitting(true);
    try {
      const file = await prepareFolderArchive(bulkFiles, controller.signal);
      controller.signal.throwIfAborted();
      requireSessionVersion(session);
      queueArchive(file);
    } catch (error) {
      if (controller.signal.aborted || session !== getSessionVersion()) return;
      toast.error(
        error instanceof Error && error.message === "folder_size_limit"
          ? uiText(
              "Folder review accepts up to 256 MiB. Choose separate Models or upload an archive prepared on your computer.",
            )
          : uiText(
              "The folder could not be prepared. Check for conflicting paths or choose a smaller folder (up to 2000 files).",
            ),
      );
      setSubmitting(false);
    } finally {
      if (folderPreparation.current === controller) folderPreparation.current = null;
    }
  }

  function doSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (submitting || pendingTagCount > 0 || (sourceMode && destinationBlocked)) return;
    if (submissionKind === "zip") {
      doInspectZip();
      return;
    }
    if (submissionKind === "bulk") {
      if (hasFolder && folderReview && !sourceSelected) {
        void reviewFolder();
        return;
      }
      if (modelFiles.length === 0) return;
      if (!collectionGate()) return;
      void runBulkModelUpload(
        {
          files: modelFiles,
          collection: collectionPath,
          tagsForUpload: [...selectedTags],
          libraryId: targetLibraryId,
        },
        onUploaded,
      );
      toast.success(
        uiText("uploads.queued", {
          value1: String(modelFiles.length),
          count: Number(modelFiles.length),
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

  async function doCreateTag() {
    if (!trimmedTagInput) return;
    if (matchingTag) {
      if (!selectedTags.includes(matchingTag.slug)) toggleTag(matchingTag.slug);
      return;
    }
    const scope = tagScope.current;
    setPendingTagCount((count) => count + 1);
    try {
      const t = await createTag({ name: trimmedTagInput });
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
                : mode === "bulk"
                  ? uiText("Add many Models from files, a folder or an archive.")
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
          <div className="mx-6 mt-4 rounded border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning font-mono">
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
            className={onboarding ? "upload-mode-content space-y-5" : "upload-mode-content"}
            role={onboarding ? undefined : "tabpanel"}
            aria-label={onboarding ? undefined : t(`uploads.mode.${mode}`)}
          >
            {/* Input area */}
            <div ref={modeMotion} className={onboarding ? undefined : "upload-source-region"}>
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
                        setMeshFile(
                          file && isSourceFile(file.name) && !isGcodeFile(file.name) ? file : null,
                        );
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
                <div className="upload-source upload-file-slots">
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
                    dropLabel={uiText("Drop a 3D model or")}
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
                    dropLabel={uiText("Drop G-code or")}
                    inputRef={gcodeRef}
                  />
                </div>
              ) : (
                <>
                  {hasFolder && (
                    <fieldset className="mb-4 space-y-2 rounded border border-border p-3 text-sm">
                      <legend className="px-1 font-medium">{uiText("Folder intake")}</legend>
                      {!sourceSelected && (
                        <label className="flex items-center gap-2">
                          <input
                            type="radio"
                            name="folder-intake"
                            checked={folderReview}
                            onChange={() => setFolderReview(true)}
                          />
                          {uiText("Review grouping and guides before importing")}
                        </label>
                      )}
                      <label className="flex items-center gap-2">
                        <input
                          type="radio"
                          name="folder-intake"
                          checked={!folderReview || sourceSelected}
                          onChange={() => setFolderReview(false)}
                        />
                        {uiText("Import separate Models directly")}
                      </label>
                      <p className="text-muted-foreground">
                        {folderReview && !sourceSelected
                          ? uiText(
                              "Next: choose separate Models or a Multipart, edit piece names and select Documents. Files use managed storage.",
                            )
                          : uiText(
                              "Only Model files will be imported. Images, guides and other files will be omitted.",
                            )}
                      </p>
                    </fieldset>
                  )}
                  <BulkFiles
                    items={bulkFiles}
                    archive={zipFile}
                    fileInputRef={bulkRef}
                    folderInputRef={bulkFolderRef}
                    onAddItems={addBulkItems}
                    onRemove={(idx) =>
                      setBulkSelection((prev) =>
                        prev.kind === "bulk"
                          ? { kind: "bulk", items: prev.items.filter((_, i) => i !== idx) }
                          : prev,
                      )
                    }
                    onClear={() => setBulkSelection(EMPTY_BULK)}
                    onRemoveArchive={() => setBulkSelection(EMPTY_BULK)}
                  />
                </>
              )}
            </div>

            {!onboarding && (
              <h4 id="upload-destination-heading" className="upload-destination-heading">
                {uiText("Library destination")}
              </h4>
            )}
            <div
              className={
                onboarding
                  ? "space-y-5"
                  : mode === "bulk" && sourceMode
                    ? "upload-destination-grid upload-destination-grid-three"
                    : "upload-destination-grid"
              }
              role={onboarding ? undefined : "group"}
              aria-labelledby={onboarding ? undefined : "upload-destination-heading"}
            >
              {mode === "files" && (
                <div>
                  <label
                    htmlFor="upload-model-name"
                    className="upload-field-label block text-sm font-medium text-on-surface mb-2"
                  >
                    {uiText("Model name")}
                  </label>
                  <input
                    id="upload-model-name"
                    value={modelName}
                    onChange={(e) => setModelName(e.target.value)}
                    className={
                      onboarding
                        ? "upload-field h-10 w-full border border-input bg-field-surface px-3 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-ring"
                        : "upload-field"
                    }
                    placeholder={
                      onboarding ? uiText("e.g. Bracket v2") : uiText("Uses the filename if blank")
                    }
                  />
                  {onboarding && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {uiText("Optional. Uses the selected filename when left blank.")}
                    </p>
                  )}
                </div>
              )}

              {/* First uploads need no collection or tags; these remain in the library upload dialog. */}
              {!onboarding && (
                <>
                  {/* Collection */}
                  <div>
                    <label htmlFor="upload-collection" className="upload-field-label">
                      {uiText("Collection")}
                    </label>
                    <DropdownMenu
                      open={catOpen}
                      onOpenChange={setCatOpen}
                      portal
                      align="start"
                      role="dialog"
                      field
                      contentClassName="upload-field-panel"
                      trigger={
                        <button
                          type="button"
                          data-menu-trigger
                          ref={collectionTrigger}
                          id="upload-collection"
                          aria-label={uiText("Collection")}
                          onClick={() => setCatOpen((v) => !v)}
                          aria-haspopup="dialog"
                          aria-expanded={catOpen}
                          title={selectedCollectionLabel || undefined}
                          className="upload-field dropdown-field flex items-center gap-2"
                        >
                          <span className="min-w-0 flex-1 truncate text-left">
                            {selectedCollectionLabel ||
                              (user?.is_superuser ? uiText("None") : uiText("Choose collection"))}
                          </span>
                          <ChevronDown
                            className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                            aria-hidden
                          />
                        </button>
                      }
                    >
                      <CollectionPicker
                        autoFocus
                        minRole="edit"
                        selectedPath={collectionPath}
                        onSelect={(collection) => {
                          setPickedCollection(collection ?? "none");
                          setCatOpen(false);
                          collectionTrigger.current?.focus();
                        }}
                        noneLabel={user?.is_superuser ? uiText("None") : undefined}
                        emptyLabel={uiText("No editable collections.")}
                      />
                    </DropdownMenu>
                  </div>

                  {/* Archive review owns the storage destination for archives. */}
                  {sourceMode && (
                    <div>
                      <label htmlFor="upload-store-in" className="upload-field-label">
                        {uiText("Store in")}
                      </label>
                      <select
                        id="upload-store-in"
                        onFocus={(event) => {
                          if (!(event.target instanceof HTMLOptionElement)) return;
                          // The native picker focuses an option after anchor positioning.
                          const trigger = event.currentTarget;
                          trigger.dataset.pickerSide =
                            event.target.getBoundingClientRect().top <
                            trigger.getBoundingClientRect().top
                              ? "above"
                              : "below";
                        }}
                        value={targetLibraryId}
                        onChange={(e) =>
                          setTargetLibraryId(e.target.value === "" ? "" : Number(e.target.value))
                        }
                        className="upload-field upload-store-in"
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
                    </div>
                  )}

                  <div className="min-w-0">
                    <label htmlFor="upload-tags" className="upload-field-label">
                      {uiText("Tags")}
                    </label>
                    <DropdownMenu
                      portal
                      field
                      role="dialog"
                      align="start"
                      aria-label={uiText("Tags")}
                      open={tagSuggestionsOpen && !!tagInput && tagItems.length > 0}
                      onOpenChange={setTagSuggestionsOpen}
                      contentClassName="upload-field-panel"
                      trigger={
                        <div
                          className="upload-field upload-tags-field"
                          onClick={(event) => {
                            if (event.target === event.currentTarget) tagField.current?.focus();
                          }}
                        >
                          {selectedTags.map((slug) => {
                            const name = tags.find((tag) => tag.slug === slug)?.name ?? slug;
                            return (
                              <span
                                key={slug}
                                className="inline-flex min-w-0 max-w-full items-center gap-1 bg-accent py-0.5 pl-1.5 pr-1 text-xs text-accent-foreground"
                              >
                                <span className="truncate" title={name}>
                                  {name}
                                </span>
                                <button
                                  type="button"
                                  onClick={() => toggleTag(slug)}
                                  aria-label={uiText("Remove {value1}", { value1: name })}
                                  className="flex h-4 w-4 shrink-0 items-center justify-center hover:bg-popover-hover focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                >
                                  <X className="h-3 w-3" aria-hidden />
                                </button>
                              </span>
                            );
                          })}
                          <input
                            id="upload-tags"
                            ref={tagField}
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
                            placeholder={uiText("Add tags")}
                            className="min-w-16 flex-1 basis-16 bg-transparent outline-none"
                          />
                        </div>
                      }
                    >
                      <div id={tagNav.listboxId} role="listbox" aria-label={uiText("Tags")}>
                        {shownTags.map((tag, i) => (
                          <button
                            key={tag.id}
                            id={tagNav.optionId(i)}
                            role="option"
                            aria-selected={i === tagNav.activeIndex}
                            data-active={i === tagNav.activeIndex}
                            type="button"
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => {
                              toggleTag(tag.slug);
                              setTagInput("");
                              tagField.current?.focus();
                            }}
                            className="field-option"
                          >
                            <span className="min-w-0 flex-1 truncate">{tag.name}</span>
                            <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
                              ({tag.model_count})
                            </span>
                          </button>
                        ))}
                        {canCreateNewTag && (
                          <button
                            type="button"
                            id={tagNav.optionId(shownTags.length)}
                            role="option"
                            aria-selected={shownTags.length === tagNav.activeIndex}
                            data-active={shownTags.length === tagNav.activeIndex}
                            onMouseDown={(event) => event.preventDefault()}
                            onClick={() => {
                              void doCreateTag();
                              setTagInput("");
                              tagField.current?.focus();
                            }}
                            className="field-option"
                          >
                            <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden />
                            <span className="truncate">
                              {uiText('Create "{value1}"', { value1: tagInput.trim() })}
                            </span>
                          </button>
                        )}
                      </div>
                    </DropdownMenu>
                  </div>
                </>
              )}
            </div>
            {!onboarding && sourceMode && (libraries.length > 0 || sourceSelected) && (
              <p className="mt-2 text-xs text-muted-foreground">
                {uiText(
                  "Eligible mounted sources use create-only write-back. Revisions to a linked model follow that source automatically.",
                )}
              </p>
            )}
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
          </div>
          <div className="upload-dialog-actions flex justify-end gap-2 border-t border-border bg-plate px-6 py-4">
            {!onboarding && (
              <p className="upload-storage-note min-w-0 flex-1 text-xs text-muted-foreground">
                {uiText(
                  "PrintStash stores existing files. It does not slice Models or verify G-code safety.",
                )}
              </p>
            )}
            <Button type="button" variant="outline" onClick={close}>
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
                (submissionKind === "files"
                  ? !meshFile && !gcodeFile
                  : submissionKind === "bulk"
                    ? modelFiles.length === 0
                    : !zipFile)
              }
            >
              {submitting ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {submissionKind === "zip" ? uiText("Preparing archive…") : uiText("Working…")}
                </>
              ) : submissionKind === "bulk" ? (
                hasFolder && folderReview && !sourceSelected ? (
                  uiText("Prepare folder review")
                ) : modelFiles.length > 0 ? (
                  uiText("counts.uploadModels", { count: modelFiles.length })
                ) : (
                  uiText("Upload to vault")
                )
              ) : submissionKind === "zip" ? (
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
  dropLabel,
  inputRef,
}: {
  label: string;
  accept: string;
  file: File | null;
  setFile: (f: File | null) => void;
  placeholder: string;
  dropLabel: string;
  inputRef: React.RefObject<HTMLInputElement | null>;
}) {
  useUiLocale();
  const [dragActive, setDragActive] = useState(false);
  // Count drag enter/leave so crossing a child element doesn't flicker the
  // highlight off — only a real leave (depth back to 0) clears it.
  const dragDepth = useRef(0);
  const contentMotion = useContentReveal(file ? "selected" : "empty");
  return (
    <div className="upload-file-slot">
      <span className="upload-field-label mb-2 block text-sm text-foreground">{label}</span>
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
        data-dragging={dragActive}
        data-empty={!file}
        className="upload-drop-zone"
      >
        <div ref={contentMotion} className="upload-slot-content">
          {file ? (
            <div className="flex w-full min-w-0 items-center gap-2">
              <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span className="min-w-0 flex-1 wrap-anywhere text-left text-xs font-medium">
                {file.name}
              </span>
              <span className="shrink-0 font-mono text-3xs text-muted-foreground">
                {formatBytes(file.size)}
              </span>
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  setFile(null);
                }}
                aria-label={uiText("Remove {value1}", { value1: label })}
                className="flex h-5 w-5 shrink-0 items-center justify-center text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="upload-drop-icon" aria-hidden />
              <p className="text-field text-muted-foreground">
                {dropLabel}{" "}
                <button
                  type="button"
                  className="upload-browse"
                  aria-label={uiText("Choose file for {value1}", { value1: label })}
                  onClick={(event) => {
                    event.stopPropagation();
                    inputRef.current?.click();
                  }}
                >
                  {uiText("choose a file")}
                </button>
              </p>
              <p className="text-2xs text-muted-foreground">{placeholder}</p>
            </>
          )}
        </div>
      </div>
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
        className="hidden"
      />
    </div>
  );
}

const bulkAccept = `${SOURCE_ACCEPT},${ARCHIVE_ACCEPT}`;

// Exported for unit tests; not used outside this module.
export function BulkFiles({
  items,
  archive,
  onRemoveArchive,
  fileInputRef,
  folderInputRef,
  onAddItems,
  onRemove,
  onClear,
}: {
  items: BulkItem[];
  archive?: File | null;
  onRemoveArchive?: () => void;
  fileInputRef: React.RefObject<HTMLInputElement | null>;
  folderInputRef: React.RefObject<HTMLInputElement | null>;
  onAddItems: (items: BulkItem[]) => void;
  onRemove: (index: number) => void;
  onClear: () => void;
}) {
  useUiLocale();
  // An existing queue is revealed with its tab. Only files added here get a row entrance.
  const [initialQueue] = useState(() => ({
    files: new Set(items.map(({ file }) => file)),
    archive,
  }));
  const [dragActive, setDragActive] = useState(false);
  // See FileSlot: count enter/leave depth so dragging over child elements
  // (icon, hint text, the "select a folder" button) doesn't flicker the
  // highlight off.
  const dragDepth = useRef(0);
  const totalBytes = items.reduce((sum, it) => sum + it.file.size, 0);
  const folderCount = new Set(items.map((it) => it.relPath).filter(Boolean)).size;

  const inputVersion = useRef(0);
  useLayoutEffect(() => {
    return () => {
      inputVersion.current += 1;
    };
  }, []);

  function handleDrop(e: React.DragEvent) {
    e.preventDefault();
    dragDepth.current = 0;
    setDragActive(false);
    const version = ++inputVersion.current;
    const dt = e.dataTransfer;
    // Snapshot entries before the browser clears DataTransfer.
    const entries = entriesFromDataTransfer(dt.items);
    if (entries.length > 0) {
      walkEntries(entries)
        .then((dropped) => {
          if (inputVersion.current === version) onAddItems(dropped);
        })
        .catch((err) => {
          if (inputVersion.current === version) {
            toast.error(err instanceof Error ? err : new Error("Couldn't read the dropped folder"));
          }
        });
    } else if (dt.files?.length) {
      onAddItems(fileListToItems(dt.files));
    }
  }

  function handlePick(event: React.ChangeEvent<HTMLInputElement>) {
    if (event.target.files?.length) {
      inputVersion.current += 1;
      onAddItems(fileListToItems(event.target.files));
    }
    event.target.value = "";
  }

  const compact = items.length > 0 || !!archive;
  return (
    <div className="upload-source upload-bulk-source">
      <div
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
        onClick={() => fileInputRef.current?.click()}
        data-dragging={dragActive}
        data-compact={compact}
        className="upload-drop-zone upload-bulk-drop cursor-action"
      >
        <Upload className="upload-drop-icon" aria-hidden />
        <p className="text-field text-muted-foreground">
          {uiText("Drop Model files, a folder or an archive")}
        </p>
        <div className="flex flex-wrap items-center justify-center gap-2 text-field">
          <button
            type="button"
            className="upload-browse"
            onClick={(event) => {
              event.stopPropagation();
              fileInputRef.current?.click();
            }}
          >
            {uiText("Choose files")}
          </button>
          <span className="text-muted-foreground" aria-hidden>
            ·
          </span>
          <button
            type="button"
            className="upload-browse"
            onClick={(event) => {
              event.stopPropagation();
              folderInputRef.current?.click();
            }}
          >
            {uiText("Choose folder")}
          </button>
        </div>
        {!compact && (
          <p className="text-2xs text-muted-foreground">{bulkAccept.replaceAll(",", " ")}</p>
        )}
      </div>
      {!archive && (
        <p className="text-xs text-muted-foreground">
          {uiText(
            "Each Model file creates a separate Model. Subfolders become nested Collections.",
          )}
        </p>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept={bulkAccept}
        multiple
        onChange={handlePick}
        className="hidden"
      />
      <input
        ref={folderInputRef}
        type="file"
        webkitdirectory=""
        multiple
        onChange={handlePick}
        className="hidden"
      />
      {archive && (
        <div
          key={`${archive.name}:${archive.size}:${archive.lastModified}`}
          className={`flex items-center gap-2 border border-border bg-muted px-3 py-2 ${archive === initialQueue.archive ? "" : "animate-card-arrive"}`}
        >
          <FileIcon className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="min-w-0 flex-1 truncate text-xs">{archive.name}</span>
          <span className="shrink-0 font-mono text-3xs text-muted-foreground">
            {formatBytes(archive.size)}
          </span>
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={onRemoveArchive}
            aria-label={uiText("Remove {value1}", { value1: archive.name })}
          >
            <X className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}
      {items.length > 0 && (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs text-muted-foreground">
              {uiText("counts.files", { count: items.length })}
              {folderCount > 0
                ? uiText("folders.count", { value1: String(folderCount), count: folderCount })
                : ""}{" "}
              · {formatBytes(totalBytes)}
            </span>
            <Button type="button" variant="ghost" size="sm" onClick={onClear}>
              {uiText("Clear all")}
            </Button>
          </div>
          <div className="rounded border border-outline-variant divide-y divide-outline-variant max-h-56 overflow-y-auto">
            {items.map((it, idx) => (
              <div
                key={`${it.relPath}/${it.file.name}:${it.file.size}`}
                className={`flex items-center gap-2 px-3 py-2 ${initialQueue.files.has(it.file) ? "" : "animate-card-arrive"}`}
              >
                <FileIcon className="h-4 w-4 text-muted-foreground flex-shrink-0" />
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
