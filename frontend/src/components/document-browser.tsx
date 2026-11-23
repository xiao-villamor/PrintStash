"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useId, useRef, useState } from "react";
import { FileText, FileType2, Loader2 } from "lucide-react";

import { MODEL_DOCUMENT_PAGE_SIZE } from "@/lib/api/documents";
import { useDocuments, useDocumentMutations } from "@/lib/queries/documents";
import { Button } from "@/components/ui/button";
import { userMessage } from "@/lib/errors";
import { useRouter } from "@/lib/navigation";
import { Link } from "@/lib/link";
import { timeAgoShort } from "@/lib/format";
import { getSessionVersion } from "@/lib/session-transport";
import { toast } from "@/lib/toast";
import type { DocumentKind, DocumentListItem } from "@/types";
import { ConfirmModal } from "@/components/ui/confirm-modal";

function KindIcon({ kind }: { kind: DocumentKind }) {
  useUiLocale();
  if (kind === "pdf") return <FileType2 className="h-4 w-4 text-muted-foreground" />;
  if (kind === "markdown") return <FileText className="h-4 w-4 text-muted-foreground" />;
  return <FileText className="h-4 w-4 text-muted-foreground" />;
}

function canEditItem(doc: DocumentListItem): boolean {
  return doc.effective_role === "edit" || doc.effective_role === "admin";
}

export function DocumentBrowser({
  collectionId,
  collectionPath,
  canCreate,
  modelId,
}: { canCreate: boolean } & (
  | { modelId: number; collectionId?: never; collectionPath?: never }
  | { modelId?: never; collectionId: number | null; collectionPath: string | null }
)) {
  useUiLocale();
  const router = useRouter();
  const headingId = useId();
  const uploadLabel = modelId === undefined ? uiText("Upload file") : uiText("Attach file");
  const [offset, setOffset] = useState(0);
  const documents = useDocuments(
    modelId === undefined
      ? { kind: "collection", path: collectionPath }
      : { kind: "model", id: modelId, offset },
  );
  const mutations = useDocumentMutations();
  const [deleteTarget, setDeleteTarget] = useState<DocumentListItem | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const items = documents.data ?? [];
  const hasNextPage = modelId !== undefined && items.length > MODEL_DOCUMENT_PAGE_SIZE;
  const docs = hasNextPage ? items.slice(0, MODEL_DOCUMENT_PAGE_SIZE) : items;
  const loading = documents.isPending;
  const busy = mutations.upload.isPending;
  const deleteBusy = mutations.remove.isPending;

  function newMarkdown() {
    // No DB row until the user saves — open the editor on the "new" route.
    const params = new URLSearchParams();
    if (collectionId != null) params.set("cid", String(collectionId));
    if (collectionPath) params.set("c", collectionPath);
    const qs = params.toString();
    router.push(`/documents/new${qs ? `?${qs}` : ""}`);
  }

  async function onFilePicked(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    const session = getSessionVersion();
    try {
      const doc = await mutations.upload.mutateAsync({
        file,
        collectionId: collectionId ?? null,
        modelId,
        session,
      });
      if (getSessionVersion() !== session) return;
      if (modelId === undefined) {
        router.push(`/documents/${doc.id}`);
      } else {
        setOffset(0);
      }
    } catch (err) {
      if (getSessionVersion() === session) toast.error(err);
    }
  }

  function remove(doc: DocumentListItem) {
    setDeleteTarget(doc);
  }

  async function confirmRemove() {
    if (!deleteTarget) return;
    const doc = deleteTarget;
    const session = getSessionVersion();
    try {
      await mutations.remove.mutateAsync({ id: doc.id, session });
      if (getSessionVersion() !== session) return;
      setDeleteTarget(null);
    } catch (err) {
      if (getSessionVersion() === session) toast.error(err);
    }
  }

  return (
    <>
      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={confirmRemove}
        busy={deleteBusy}
        title={uiText("Delete document?")}
        description={
          deleteTarget
            ? uiText('"{value1}" will be moved to trash.', { value1: String(deleteTarget.name) })
            : uiText("This document will be moved to trash.")
        }
      />
      <section
        aria-labelledby={headingId}
        className={
          modelId === undefined
            ? "mx-4 my-5 overflow-hidden border border-border bg-card sm:mx-6"
            : "overflow-hidden border border-border bg-card"
        }
      >
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4 sm:px-5">
          <div className="flex min-w-0 items-start gap-3">
            <span className="flex h-8 w-8 shrink-0 items-center justify-center bg-muted/70 text-muted-foreground">
              <FileText className="h-4 w-4" aria-hidden />
            </span>
            <div>
              <h2 id={headingId} className="text-sm font-semibold">
                {modelId === undefined ? uiText("Documents") : uiText("Attachments")}
              </h2>
              <p className="mt-1 text-xs text-muted-foreground">
                {modelId === undefined
                  ? uiText("Guides, notes and files in this collection.")
                  : uiText("Guides, photos and videos for this Model.")}
              </p>
            </div>
          </div>
          {canCreate && (
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="outline"
                size="xs"
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                loading={busy}
              >
                {uploadLabel}
              </Button>
              {modelId === undefined && (
                <Button size="xs" onClick={newMarkdown}>
                  {uiText("New document")}
                </Button>
              )}
              <input
                ref={fileRef}
                type="file"
                aria-label={uploadLabel}
                onChange={onFilePicked}
                className="hidden"
              />
            </div>
          )}
        </div>

        {documents.isError ? (
          <div role="alert" className="p-5 text-sm text-destructive">
            <p>{userMessage(documents.error)}</p>
            <Button variant="outline" onClick={() => void documents.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        ) : loading ? (
          <div className="flex items-center justify-center py-16 text-muted-foreground">
            <Loader2 className="w-5 h-5 animate-spin" />
          </div>
        ) : docs.length === 0 ? (
          <div className="flex flex-col items-start gap-1 px-5 py-6 text-muted-foreground">
            <FileText className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-sm">
              {modelId === undefined
                ? uiText("No documents here yet.")
                : uiText("No attachments yet.")}
            </p>
            {canCreate && modelId === undefined && (
              <p className="text-xs mt-1">
                {uiText(
                  "Create a markdown doc or upload any file, including spreadsheets and resin slices.",
                )}
              </p>
            )}
          </div>
        ) : (
          <div className="divide-y divide-border">
            {docs.map((doc) => (
              <div key={doc.id} className="flex flex-wrap items-center gap-3 px-4 py-3 sm:px-5">
                <Link
                  href={`/documents/${doc.id}`}
                  className="flex min-w-0 flex-1 basis-48 items-center gap-3 hover:text-primary"
                >
                  <span className="flex h-7 w-7 shrink-0 items-center justify-center bg-muted/70">
                    <KindIcon kind={doc.kind} />
                  </span>
                  <div className="min-w-0">
                    <div className="break-words text-sm font-medium text-foreground">
                      {doc.name}
                    </div>
                    <div className="mt-0.5 break-words text-xs text-muted-foreground">
                      {(modelId !== undefined && doc.filename) || doc.kind}
                      {" · "}
                      {timeAgoShort(doc.updated_at)}
                    </div>
                  </div>
                </Link>
                <div className="flex shrink-0 items-center gap-3">
                  <Button variant="outline" size="xs" asChild>
                    <Link href={`/documents/${doc.id}`}>{uiText("Open")}</Link>
                  </Button>
                  {canEditItem(doc) && (
                    <Button
                      variant="outline"
                      size="xs"
                      onClick={() => remove(doc)}
                      title={uiText("Delete document")}
                      className="text-destructive"
                    >
                      {uiText("Delete document")}
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        {modelId !== undefined && (offset > 0 || hasNextPage) && (
          <div className="flex justify-end gap-2 border-t border-border p-4">
            <Button
              variant="outline"
              size="xs"
              disabled={offset === 0}
              onClick={() => setOffset(offset - MODEL_DOCUMENT_PAGE_SIZE)}
            >
              {uiText("Previous")}
            </Button>
            <Button
              variant="outline"
              size="xs"
              disabled={!hasNextPage}
              onClick={() => setOffset(offset + MODEL_DOCUMENT_PAGE_SIZE)}
            >
              {uiText("Next")}
            </Button>
          </div>
        )}
      </section>
    </>
  );
}
