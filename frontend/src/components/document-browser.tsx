"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, FileType2, Loader2, Plus, Trash2, Upload } from "lucide-react";

import { deleteDocument, listDocuments, uploadDocument } from "@/lib/api";
import { Link, useRouter } from "@/lib/navigation";
import { timeAgoShort } from "@/lib/format";
import { toast } from "@/lib/toast";
import type { DocumentKind, DocumentListItem } from "@/types";
import { ConfirmModal } from "@/components/ui/confirm-modal";

function KindIcon({ kind }: { kind: DocumentKind }) {
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
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<DocumentListItem | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  function load() {
    setLoading(true);
    listDocuments(collectionPath, { fresh: true })
      .then(setDocs)
      .catch(() => setDocs([]))
      .finally(() => setLoading(false));
  }

  useEffect(load, [collectionPath]);

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
    setBusy(true);
    try {
      const doc = await uploadDocument(file, collectionId);
      router.push(`/documents/${doc.id}`);
    } catch (err) {
      toast.error(err);
      setBusy(false);
    }
  }

  function remove(doc: DocumentListItem) {
    setDeleteTarget(doc);
  }

  async function confirmRemove() {
    if (!deleteTarget) return;
    const doc = deleteTarget;
    setDeleteBusy(true);
    try {
      await deleteDocument(doc.id);
      setDocs((ds) => ds.filter((d) => d.id !== doc.id));
      setDeleteTarget(null);
    } catch (err) {
      toast.error(err);
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <>
      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={confirmRemove}
        busy={deleteBusy}
        title="Delete document?"
        description={deleteTarget ? `"${deleteTarget.name}" will be moved to trash.` : "This document will be moved to trash."}
      />
      <div className="p-4 sm:p-6">
      {canCreate && (
        <div className="flex items-center gap-2 mb-4">
          <button
            onClick={newMarkdown}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover"
          >
            <Plus className="w-4 h-4" />
            New document
          </button>
          <button
            onClick={() => fileRef.current?.click()}
            disabled={busy}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted disabled:opacity-50"
          >
            <Upload className="w-4 h-4 text-muted-foreground" /> Upload PDF / file
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.md,.markdown,.txt"
            onChange={onFilePicked}
            className="hidden"
          />
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin" />
        </div>
      ) : docs.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center text-muted-foreground">
          <FileText className="w-8 h-8 mb-2 opacity-40" />
          <p className="text-sm">No documents here yet.</p>
          {canCreate && <p className="text-xs mt-1">Create a markdown doc or upload a PDF.</p>}
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(240px,1fr))]">
          {docs.map((doc) => (
            <div
              key={doc.id}
              className="group relative flex items-start gap-3 rounded-lg border border-border bg-background p-3 hover:border-primary transition-colors"
            >
              <Link href={`/documents/${doc.id}`} className="flex items-start gap-3 min-w-0 flex-1">
                <KindIcon kind={doc.kind} />
                <div className="min-w-0">
                  <div className="text-sm font-medium text-foreground truncate">{doc.name}</div>
                  <div className="text-xs text-muted-foreground mt-0.5 uppercase font-mono">
                    {doc.kind} · {timeAgoShort(doc.updated_at)}
                  </div>
                </div>
              </Link>
              {canEditItem(doc) && (
                <button
                  onClick={() => remove(doc)}
                  title="Delete document"
                  className="opacity-0 group-hover:opacity-100 p-1 text-muted-foreground hover:text-red-600 transition-opacity"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}
      </div>
    </>
  );
}
