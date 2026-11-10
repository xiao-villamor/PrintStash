"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useState } from "react";
import { Download, FileText, FolderSync, MoreHorizontal, RotateCcw, Trash2 } from "lucide-react";

import {
  downloadAuthenticatedFile,
  replaceFileTags,
  restoreSourceFile,
  trashSourceFile,
} from "@/lib/api";
import type { StlPreviewFailure } from "@/lib/use-stl-preview";
import { formatBytes } from "@/lib/format";
import { useTags } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { FileRead, ModelRead, TrashedSourceFileRead } from "@/types";

import { HashValue } from "./file-hashes";
import { DerivativeStatus } from "@/components/derivative-status";
import { SlicerOpenButton } from "@/components/slicer-open-button";
import { Localized } from "@/components/ui/localized";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { DropdownMenu } from "@/components/ui/dropdown-menu";

const SLICEABLE_TYPES = new Set(["stl", "3mf", "obj"]);

export function FilesTab({
  modelId,
  sourceFiles,
  previewFailure = null,
  trashedSourceFiles = [],
  canEdit,
  onModel,
  onCompare,
}: {
  modelId: number;
  sourceFiles: FileRead[];
  previewFailure?: { fileId: number; reason: StlPreviewFailure } | null;
  trashedSourceFiles?: TrashedSourceFileRead[];
  canEdit: boolean;
  onModel: (model: ModelRead) => void;
  onCompare?: () => void;
}) {
  useUiLocale();
  const { data: availableTags = [] } = useTags();
  const [menuFileId, setMenuFileId] = useState<number | null>(null);
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
        <div className="mb-2 flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-base font-semibold">
            {uiText("Source files")}{" "}
            <span className="ml-1 text-xs font-normal text-muted-foreground">
              {sourceFiles.length}
            </span>
          </h2>
          {onCompare && (
            <Button variant="ghost" size="xs" onClick={onCompare}>
              {uiText("Compare")}
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
        <div>
          {sourceFiles.map((f) => (
            <article
              key={f.id}
              aria-label={f.original_filename}
              className="@container/source-file min-w-0 space-y-3 border-b border-border py-5.5"
            >
              <div className="flex min-w-0 flex-col gap-3">
                <div className="flex min-w-0 items-start gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <div className="min-w-0">
                    <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm text-on-surface font-medium">
                      <span className="min-w-0 break-all">{f.original_filename}</span>
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
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      {f.file_type.toUpperCase()} ·{" "}
                      {uiText("v{value1}", { value1: String(f.version) })} ·{" "}
                      {formatBytes(f.size_bytes)} · {new Date(f.uploaded_at).toLocaleDateString()}
                    </p>
                  </div>
                </div>
                <dl className="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)] items-center gap-x-3 gap-y-2 text-xs">
                  <dt className="text-muted-foreground">{uiText("File SHA-256")}</dt>
                  <dd>
                    <HashValue
                      sha={f.sha256}
                      label={uiText("File SHA-256")}
                      filename={f.original_filename}
                    />
                  </dd>
                  {f.geometric_sha256 !== null && (
                    <>
                      <dt className="text-muted-foreground">{uiText("Geometry SHA-256")}</dt>
                      <dd>
                        <HashValue
                          sha={f.geometric_sha256}
                          label={uiText("Geometry SHA-256")}
                          filename={f.original_filename}
                        />
                      </dd>
                    </>
                  )}
                </dl>
                <div className="flex flex-wrap items-center gap-1">
                  {SLICEABLE_TYPES.has(f.file_type) && (
                    <SlicerOpenButton fileId={f.id} fileType={f.file_type} size="sm" showLabel />
                  )}
                  <Button
                    type="button"
                    variant="ghost"
                    size="xs"
                    onClick={() =>
                      downloadAuthenticatedFile(
                        `/api/v1/files/${f.id}/download`,
                        f.original_filename,
                      ).catch((e) => toast.error(e))
                    }
                    title={uiText("Download")}
                  >
                    <Download className="h-3.5 w-3.5" />
                    {uiText("Download")}
                  </Button>
                  <EntityTagsDialog
                    triggerMode="command"
                    entityLabel={f.original_filename}
                    tags={f.tags}
                    availableTags={availableTags}
                    canEdit={canEdit}
                    help={uiText(
                      "Artifact tags make the owning Model discoverable without changing the Model’s direct tags.",
                    )}
                    onSave={(tags) => saveTags(f, tags)}
                  />
                  <DropdownMenu
                    open={menuFileId === f.id}
                    onOpenChange={(open) => setMenuFileId(open ? f.id : null)}
                    trigger={
                      <Button
                        type="button"
                        data-menu-trigger
                        variant="ghost"
                        size="xs"
                        aria-haspopup="menu"
                        aria-expanded={menuFileId === f.id}
                        aria-label={uiText("Actions for {value1}", {
                          value1: f.original_filename,
                        })}
                        onClick={() => setMenuFileId(menuFileId === f.id ? null : f.id)}
                      >
                        <MoreHorizontal className="h-3.5 w-3.5" />
                        {uiText("nav.more")}
                      </Button>
                    }
                    contentClassName="w-56 rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-lg"
                  >
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
                        className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-left text-sm text-destructive hover:bg-destructive/10 focus-visible:bg-destructive/10 focus-visible:outline-none disabled:pointer-events-none disabled:opacity-50"
                      >
                        <Trash2 className="h-4 w-4" />
                        {uiText("Move to trash")}
                      </button>
                    )}
                  </DropdownMenu>
                </div>
              </div>
              <DerivativeStatus
                modelId={modelId}
                fileId={f.id}
                canRetry={canEdit}
                previewFailure={previewFailure?.fileId === f.id ? previewFailure.reason : null}
              />
            </article>
          ))}
        </div>
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
