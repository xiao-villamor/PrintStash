"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useState, type ReactNode } from "react";
import { FileText, FolderSync, MoreHorizontal, RotateCcw, Tags, Trash2 } from "lucide-react";

import {
  downloadAuthenticatedFile,
  replaceFileTags,
  restoreSourceFile,
  trashSourceFile,
} from "@/lib/api";
import type { StlPreviewFailure } from "@/lib/use-stl-preview";
import { formatBytes } from "@/lib/format";
import { useTags } from "@/lib/queries";
import { copyText } from "@/lib/clipboard";
import { toast } from "@/lib/toast";
import { FileRead, ModelRead, TrashedSourceFileRead } from "@/types";

import { DerivativeStatus } from "@/components/derivative-status";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { Localized } from "@/components/ui/localized";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { PropertyList, PropertyRow, SoftCard } from "./inspector-section";

const SLICEABLE_TYPES = new Set(["stl", "3mf", "obj"]);

export function FilesTab({
  modelId,
  sourceFiles,
  previewFailure = null,
  trashedSourceFiles = [],
  canEdit,
  onModel,
  onCompare,
  fileFacts,
  sourcePlateCount = null,
}: {
  modelId: number;
  sourceFiles: FileRead[];
  previewFailure?: { fileId: number; reason: StlPreviewFailure } | null;
  trashedSourceFiles?: TrashedSourceFileRead[];
  canEdit: boolean;
  onModel: (model: ModelRead) => void;
  onCompare?: () => void;
  /** Extra fact rows for one source file, such as measured geometry. */
  fileFacts?: (file: FileRead) => ReactNode;
  sourcePlateCount?: { fileId: number; count: number } | null;
}) {
  useUiLocale();
  const { data: availableTags = [] } = useTags();
  const [menuFileId, setMenuFileId] = useState<number | null>(null);
  const [tagsDialog, setTagsDialog] = useState<{ fileId: number | null; opened: number }>({
    fileId: null,
    opened: 0,
  });
  const [deleteTarget, setDeleteTarget] = useState<FileRead | null>(null);
  const [busyFileId, setBusyFileId] = useState<number | null>(null);

  async function confirmTrash() {
    if (!deleteTarget) return;
    setBusyFileId(deleteTarget.id);
    try {
      onModel(await trashSourceFile(modelId, deleteTarget.id));
      toast.success(uiText("Source file moved to trash"));
      setDeleteTarget(null);
    } catch (error) {
      toast.error(error);
    } finally {
      setBusyFileId(null);
    }
  }

  async function restore(file: TrashedSourceFileRead) {
    setBusyFileId(file.id);
    try {
      onModel(await restoreSourceFile(modelId, file.id));
      toast.success(uiText("Source file restored"));
    } catch (error) {
      toast.error(error);
    } finally {
      setBusyFileId(null);
    }
  }

  async function saveTags(file: FileRead, tags: string[]) {
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
      <section>
        <ConfirmModal
          open={deleteTarget !== null}
          onClose={() => setDeleteTarget(null)}
          onConfirm={confirmTrash}
          busy={busyFileId !== null}
          title={uiText("Move source file to trash?")}
          description={
            deleteTarget
              ? uiText("{value1} will be hidden from this Model. Other files and Revisions stay.", {
                  value1: deleteTarget.original_filename,
                })
              : ""
          }
          confirmLabel={uiText("Move to trash")}
        />
        <div className="mb-2 flex min-h-7 flex-wrap items-center justify-between gap-3">
          <h2 className="px-1 text-sm font-medium text-muted-foreground">
            {uiText("Source files")} · {sourceFiles.length}
          </h2>
          {onCompare && (
            <Button variant="ghost" size="xs" data-compare-origin onClick={onCompare}>
              {uiText("Compare source files")}
            </Button>
          )}
        </div>
        {sourceFiles.length === 0 && (
          <p className="font-mono text-xs text-on-surface-variant">
            {uiText(
              "No source files for this Model. Revisions and print history remain available.",
            )}
          </p>
        )}
        <div className="space-y-3">
          {sourceFiles.map((f) => {
            const facts = fileFacts?.(f);
            return (
              <article
                key={f.id}
                id={`artifact-${f.id}`}
                aria-label={f.original_filename}
                className="min-w-0"
              >
                <SoftCard>
                  <div className="space-y-3 px-4 py-3">
                    <div className="flex min-w-0 items-start gap-3">
                      <FileText className="mt-0.5 size-[18px] shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1 space-y-0.5">
                        <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm font-medium text-foreground">
                          <span className="min-w-0 wrap-anywhere">{f.original_filename}</span>
                          {f.is_external && (
                            <span
                              title={uiText(
                                "Indexed from a library source; original bytes stay in external storage",
                              )}
                              className="inline-flex shrink-0 items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider text-primary"
                            >
                              <FolderSync className="h-3 w-3" />
                              {uiText("Linked")}
                            </span>
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {f.file_type.toUpperCase()} ·{" "}
                          {uiText("v{value1}", { value1: String(f.version) })} ·{" "}
                          {formatBytes(f.size_bytes)} ·{" "}
                          {new Date(f.uploaded_at).toLocaleDateString()}
                        </p>
                      </div>
                      <DropdownMenu
                        open={menuFileId === f.id}
                        onOpenChange={(open) => setMenuFileId(open ? f.id : null)}
                        portal
                        align="end"
                        trigger={
                          <Button
                            type="button"
                            data-menu-trigger
                            variant="ghost"
                            size="icon-sm"
                            className="-my-1 -me-2 size-8"
                            aria-haspopup="menu"
                            aria-expanded={menuFileId === f.id}
                            aria-label={uiText("Actions for {value1}", {
                              value1: f.original_filename,
                            })}
                            onClick={() => setMenuFileId(menuFileId === f.id ? null : f.id)}
                          >
                            <MoreHorizontal className="size-4" aria-hidden />
                          </Button>
                        }
                        contentClassName="w-56 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
                      >
                        {canEdit && (
                          <button
                            type="button"
                            role="menuitem"
                            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
                            onClick={() => {
                              setMenuFileId(null);
                              setTagsDialog({ fileId: f.id, opened: tagsDialog.opened + 1 });
                            }}
                          >
                            <Tags className="size-4" aria-hidden />
                            {f.tags.length ? uiText("Edit tags") : uiText("Add tags")}
                          </button>
                        )}
                        {f.is_external ? (
                          <p className="px-3 py-2 text-xs text-muted-foreground">
                            {uiText("Linked source files stay in their Library source.")}
                          </p>
                        ) : (
                          <button
                            type="button"
                            role="menuitem"
                            disabled={!canEdit || busyFileId !== null}
                            onClick={() => {
                              setMenuFileId(null);
                              setDeleteTarget(f);
                            }}
                            className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10 focus-visible:bg-destructive/10 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                          >
                            <Trash2 className="size-4" aria-hidden />
                            {uiText("Move to trash")}
                          </button>
                        )}
                      </DropdownMenu>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 ps-[30px]">
                      {SLICEABLE_TYPES.has(f.file_type) && (
                        <SlicerOpenButton
                          fileId={f.id}
                          fileType={f.file_type}
                          size="sm"
                          showLabel
                          outline
                        />
                      )}
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          downloadAuthenticatedFile(
                            `/api/v1/files/${f.id}/download`,
                            f.original_filename,
                          ).catch((e) => toast.error(e))
                        }
                      >
                        {uiText("Download")}
                      </Button>
                    </div>
                  </div>
                  <DerivativeStatus
                    modelId={modelId}
                    fileId={f.id}
                    canRetry={canEdit}
                    showReady
                    plateCount={sourcePlateCount?.fileId === f.id ? sourcePlateCount.count : null}
                    className="space-y-0 bg-muted/40 px-4 py-2.5"
                    previewFailure={previewFailure?.fileId === f.id ? previewFailure.reason : null}
                  />
                  <PropertyList label={uiText("File facts")}>
                    <PropertyRow label={uiText("SHA-256")}>
                      <code className="font-mono text-xs">
                        {f.sha256.slice(0, 4)}…{f.sha256.slice(-4)}
                      </code>
                      <Button
                        type="button"
                        variant="link"
                        size="xs"
                        className="h-auto px-1 text-xs"
                        onClick={() =>
                          void copyText(f.sha256).then(
                            () => toast.success(uiText("modelHashes.copied")),
                            () => toast.error(uiText("modelHashes.copyFailed")),
                          )
                        }
                      >
                        {uiText("Copy SHA-256")}
                      </Button>
                    </PropertyRow>
                    {facts}
                  </PropertyList>
                </SoftCard>
              </article>
            );
          })}
        </div>
        {tagsDialog.fileId !== null &&
          sourceFiles
            .filter((file) => file.id === tagsDialog.fileId)
            .map((file) => (
              <EntityTagsDialog
                key={`${file.id}-${tagsDialog.opened}`}
                initialOpen
                triggerMode="none"
                entityLabel={file.original_filename}
                tags={file.tags}
                availableTags={availableTags}
                canEdit={canEdit}
                help={uiText(
                  "Artifact tags make the owning Model discoverable without changing the Model’s direct tags.",
                )}
                onSave={(tags) => saveTags(file, tags)}
              />
            ))}
        {trashedSourceFiles.length > 0 && (
          <div className="mt-4">
            <h3 className="mb-2 text-sm font-medium text-on-surface">
              {uiText("Trashed source files")}
            </h3>
            <div className="space-y-2">
              {trashedSourceFiles.map((file) => (
                <div
                  key={file.id}
                  className="flex items-center justify-between gap-3 border-b border-dashed border-border py-3"
                >
                  <span className="min-w-0 truncate text-sm text-on-surface-variant">
                    {file.original_filename}
                  </span>
                  {canEdit && (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={busyFileId !== null}
                      onClick={() => void restore(file)}
                    >
                      <RotateCcw className="h-4 w-4" />
                      {uiText("Restore")}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </section>
    </Localized>
  );
}
