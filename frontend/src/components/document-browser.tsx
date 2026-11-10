"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useRef, useState } from "react";
import { FileText, FileType2, Loader2, Plus, Trash2, Upload } from "lucide-react";

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
  if (kind === "pdf") return <FileType2 className="w-5 h-5 text-red-500" />;
  if (kind === "markdown") return <FileText className="w-5 h-5 text-primary" />;
  return <FileText className="w-5 h-5 text-muted-foreground" />;
}

function canEditItem(doc: DocumentListItem): boolean {
  return doc.effective_role === "edit" || doc.effective_role === "admin";
}

export function DocumentBrowser({
  collectionId,
  collectionPath,
  canCreate,
}: {
  collectionId: number | null;
  collectionPath: string | null;
  canCreate: boolean;
}) {
  useUiLocale();
  const router = useRouter();
  const documents = useDocuments(collectionPath);
  const mutations = useDocumentMutations();
  const [deleteTarget, setDeleteTarget] = useState<DocumentListItem | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const docs = documents.data ?? [];
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
      const doc = await mutations.upload.mutateAsync({ file, collectionId, session });
      if (getSessionVersion() !== session) return;
      router.push(`/documents/${doc.id}`);
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
      <section className="mx-4 my-5 overflow-hidden border border-border bg-card sm:mx-6">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-4 sm:px-5">
          <div>
            <h2 className="text-sm font-semibold">{uiText("Documents")}</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              {uiText("Guides, notes and files in this collection.")}
            </p>
          </div>
          {canCreate && (
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={newMarkdown}
                className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover"
              >
                <Plus className="w-4 h-4" />
                {uiText("New document")}
              </button>
              <button
                onClick={() => fileRef.current?.click()}
                disabled={busy}
                className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted disabled:opacity-50"
              >
                <Upload className="w-4 h-4 text-muted-foreground" />
                {uiText("Upload file")}
              </button>
              <input
                ref={fileRef}
                type="file"
                aria-label={uiText("Upload file")}
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
          <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground">
            <FileText className="w-8 h-8 mb-2 opacity-40" />
            <p className="text-sm">{uiText("No documents here yet.")}</p>
            {canCreate && (
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
              <div key={doc.id} className="flex items-center gap-3 px-4 py-3 sm:px-5">
                <Link
                  href={`/documents/${doc.id}`}
                  className="flex min-w-0 flex-1 items-center gap-3 hover:text-primary"
                >
                  <KindIcon kind={doc.kind} />
                  <div className="min-w-0">
                    <div className="break-words text-sm font-medium text-foreground">
                      {doc.name}
                    </div>
                    <div className="text-xs text-muted-foreground mt-0.5 uppercase font-mono">
                      {doc.kind} · {timeAgoShort(doc.updated_at)}
                    </div>
                  </div>
                </Link>
                {canEditItem(doc) && (
                  <button
                    onClick={() => remove(doc)}
                    title={uiText("Delete document")}
                    className="p-2 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>
    </>
  );
}
