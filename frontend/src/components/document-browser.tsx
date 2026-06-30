"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, FileType2, Loader2, Plus, Trash2, Upload } from "lucide-react";

import { deleteDocument, listDocuments, uploadDocument } from "@/lib/api";
import { Link, useRouter } from "@/lib/navigation";
import { timeAgoShort } from "@/lib/format";
import { toast } from "@/lib/toast";
import { Checkbox } from "@/components/ui/checkbox";
import type { DocumentKind, DocumentListItem } from "@/types";

function KindIcon({ kind }: { kind: DocumentKind }) {
  if (kind === "pdf") return <FileType2 className="w-5 h-5 text-red-500" />;
  if (kind === "markdown") return <FileText className="w-5 h-5 text-blue-500 dark:text-orange-500" />;
  return <FileText className="w-5 h-5 text-muted-foreground" />;
}

function canEditItem(doc: DocumentListItem): boolean {
  return doc.effective_role === "edit" || doc.effective_role === "admin";
}

export function DocumentBrowser({
  collectionId,
  collectionPath,
  canCreate,
  selectMode = false,
  selectedDocumentIds = new Set(),
  onToggleDocumentSelect,
}: {
  collectionId: number | null;
  collectionPath: string | null;
  canCreate: boolean;
  selectMode?: boolean;
  selectedDocumentIds?: Set<number>;
  onToggleDocumentSelect?: (id: number) => void;
}) {
  const router = useRouter();
  const [docs, setDocs] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
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

  async function remove(doc: DocumentListItem) {
    if (!confirm(`Delete "${doc.name}"?`)) return;
    try {
      await deleteDocument(doc.id);
      setDocs((ds) => ds.filter((d) => d.id !== doc.id));
    } catch (err) {
      toast.error(err);
    }
  }

  return (
    <div className="p-4 sm:p-6">
      {canCreate && (
        <div className="flex items-center gap-2 mb-4">
          <button
            onClick={newMarkdown}
            className="flex items-center gap-1.5 px-3 py-2 text-xs font-medium text-white bg-blue-600 dark:bg-orange-600 rounded hover:bg-blue-700 dark:hover:bg-orange-700"
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
          {docs.map((doc) => {
            const isSelected = selectedDocumentIds.has(doc.id);
            return (
            <div
              key={doc.id}
                onClick={() => {
                  if (selectMode) onToggleDocumentSelect?.(doc.id);
                }}
                className={`group relative flex items-start gap-3 rounded-lg border bg-background p-3 transition-colors ${
                  selectMode ? "cursor-pointer" : ""
                } ${
                  isSelected
                    ? "border-blue-500 dark:border-orange-500 bg-blue-50/30 dark:bg-orange-950/20"
                    : "border-border hover:border-blue-400 dark:hover:border-orange-500"
                }`}
              >
                {selectMode && (
                  <span
                    className="absolute top-2 left-2 z-10"
                    onClick={(e) => { e.stopPropagation(); onToggleDocumentSelect?.(doc.id); }}
                  >
                    <Checkbox
                      checked={isSelected}
                      onChange={() => onToggleDocumentSelect?.(doc.id)}
                      ariaLabel={`Select ${doc.name}`}
                    />
                  </span>
                )}
                {selectMode ? (
                  <div className={`flex items-start gap-3 min-w-0 flex-1 ${selectMode ? "pl-6" : ""}`}>
                    <KindIcon kind={doc.kind} />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-foreground truncate">{doc.name}</div>
                      <div className="text-xs text-muted-foreground mt-0.5 uppercase font-mono">
                        {doc.kind} · {timeAgoShort(doc.updated_at)}
                      </div>
                    </div>
                  </div>
                ) : (
              <Link href={`/documents/${doc.id}`} className="flex items-start gap-3 min-w-0 flex-1">
                <KindIcon kind={doc.kind} />
                <div className="min-w-0">
                  <div className="text-sm font-medium text-foreground truncate">{doc.name}</div>
                  <div className="text-xs text-muted-foreground mt-0.5 uppercase font-mono">
                    {doc.kind} · {timeAgoShort(doc.updated_at)}
                  </div>
                </div>
              </Link>
                )}
                {!selectMode && canEditItem(doc) && (
                <button
                    onClick={(e) => { e.stopPropagation(); remove(doc); }}
                  title="Delete document"
                  className="opacity-0 group-hover:opacity-100 p-1 text-muted-foreground hover:text-red-600 transition-opacity"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              )}
            </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
