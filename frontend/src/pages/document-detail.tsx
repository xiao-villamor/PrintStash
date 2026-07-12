"use client";

import { Suspense, lazy, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { ArrowLeft, Download, Eye, Loader2, Pencil, Save } from "lucide-react";

import { MarkdownView } from "@/components/markdown-view";
import {
  createDocument,
  getAuthenticatedBlob,
  getDocument,
  updateDocument,
  uploadDocumentImage,
} from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { Link, useRouter, useSearchParams } from "@/lib/navigation";
import { toast } from "@/lib/toast";
import type { DocumentRead } from "@/types";
import NotFound from "./not-found";

// pdf.js is heavy — only pull the chunk in when a PDF is actually opened.
const PdfViewer = lazy(() =>
  import("@/components/pdf-viewer").then((m) => ({ default: m.PdfViewer })),
);

function canEditDoc(doc: DocumentRead | null, isSuper: boolean): boolean {
  if (isSuper) return true;
  return doc?.effective_role === "edit" || doc?.effective_role === "admin";
}

export default function DocumentDetailPage() {
  const { id } = useParams();
  const isNew = id === "new";
  const docId = Number(id);
  const { user } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const collectionParam = searchParams.get("c");
  const cidParam = searchParams.get("cid");
  const collectionId = cidParam ? Number(cidParam) : null;

  const [doc, setDoc] = useState<DocumentRead | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [mode, setMode] = useState<"preview" | "edit">("preview");
  const [draftBody, setDraftBody] = useState("");
  const [draftName, setDraftName] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const canEdit = canEditDoc(doc, !!user?.is_superuser);
  const backHref = doc?.collection
    ? `/?c=${encodeURIComponent(doc.collection)}&v=docs`
    : "/?v=docs";

  useEffect(() => {
    // New doc: no DB row yet — start in the editor, only POST on save.
    if (isNew) {
      setDoc({
        id: 0,
        name: "Untitled document",
        kind: "markdown",
        collection: collectionParam,
        collection_id: collectionId,
        filename: null,
        effective_role: "edit",
        updated_at: "",
        body: "",
      });
      setDraftName("Untitled document");
      setDraftBody("");
      setMode("edit");
      setLoading(false);
      return;
    }
    if (!id || Number.isNaN(docId)) {
      setNotFound(true);
      setLoading(false);
      return;
    }
    let alive = true;
    setLoading(true);
    getDocument(docId)
      .then((d) => {
        if (!alive) return;
        setDoc(d);
        setDraftBody(d.body ?? "");
        setDraftName(d.name);
      })
      .catch(() => alive && setNotFound(true))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [id, docId, isNew, collectionParam, collectionId]);

  // Fetch the PDF blob for inline viewing (auth header can't ride a raw iframe src).
  useEffect(() => {
    if (!doc || doc.kind !== "pdf") return;
    let alive = true;
    let url: string | null = null;
    getAuthenticatedBlob(`/api/v1/documents/${doc.id}/file`)
      .then((blob) => {
        if (!alive) return;
        url = URL.createObjectURL(blob);
        setPdfUrl(url);
      })
      .catch(() => alive && toast.error("Could not load PDF"));
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [doc]);

  function insertAtCursor(text: string) {
    const el = textareaRef.current;
    if (!el) {
      setDraftBody((b) => b + text);
      return;
    }
    const { selectionStart: s, selectionEnd: e } = el;
    setDraftBody((b) => b.slice(0, s) + text + b.slice(e));
  }

  async function handleImages(files: FileList | File[]) {
    const images = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (!images.length || !doc) return;
    if (isNew) {
      toast.error("Save the document before adding images.");
      return;
    }
    setUploading(true);
    try {
      for (const file of images) {
        const { url } = await uploadDocumentImage(doc.id, file);
        insertAtCursor(`\n![${file.name}](${url})\n`);
      }
    } catch (err) {
      toast.error(err);
    } finally {
      setUploading(false);
    }
  }

  async function save() {
    if (!doc) return;
    setSaving(true);
    try {
      if (isNew) {
        const created = await createDocument({
          name: draftName.trim() || "Untitled document",
          collection_id: collectionId,
          body: draftBody,
        });
        router.replace(`/documents/${created.id}`);
        return;
      }
      const updated = await updateDocument(doc.id, {
        name: draftName.trim() || doc.name,
        body: draftBody,
      });
      setDoc(updated);
      setMode("preview");
    } catch (err) {
      toast.error(err);
    } finally {
      setSaving(false);
    }
  }

  async function downloadFile() {
    if (!doc) return;
    try {
      const blob = await getAuthenticatedBlob(`/api/v1/documents/${doc.id}/file`);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = doc.filename ?? doc.name;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      toast.error(err);
    }
  }

  if (notFound) return <NotFound />;
  if (loading || !doc) {
    return <div className="min-h-screen bg-background" aria-busy="true" />;
  }

  const isMarkdown = doc.kind === "markdown";

  return (
    <div className="h-full flex flex-col bg-background">
      <div className="mx-auto w-full max-w-4xl flex flex-col flex-1 min-h-0 px-4 sm:px-6 py-6">
        <div className="flex items-center gap-3 mb-4">
          <Link
            href={backHref}
            className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="w-4 h-4" /> Back
          </Link>
          {mode === "edit" ? (
            <input
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              className="flex-1 bg-surface text-foreground text-lg font-semibold border border-border rounded px-2 py-1 focus:outline-none focus:ring-2 focus:ring-ring"
            />
          ) : (
            <h1 className="flex-1 text-xl font-bold text-foreground truncate">{doc.name}</h1>
          )}

          {isMarkdown && canEdit && mode === "preview" && (
            <button
              onClick={() => setMode("edit")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
            >
              <Pencil className="w-3.5 h-3.5" /> Edit
            </button>
          )}
          {isMarkdown && mode === "edit" && (
            <>
              <button
                onClick={() => setMode("preview")}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
              >
                <Eye className="w-3.5 h-3.5" /> Preview
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save
              </button>
            </>
          )}
          {!isMarkdown && (
            <button
              onClick={downloadFile}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
            >
              <Download className="w-3.5 h-3.5" /> Download
            </button>
          )}
        </div>

        <div className="flex-1 min-h-0 overflow-auto pb-24 md:pb-0">
        {/* Markdown: edit or preview */}
        {isMarkdown &&
          (mode === "edit" ? (
            <div className="flex flex-col h-full">
              <textarea
                ref={textareaRef}
                value={draftBody}
                onChange={(e) => setDraftBody(e.target.value)}
                onPaste={(e) => {
                  if (e.clipboardData.files.length) {
                    e.preventDefault();
                    handleImages(e.clipboardData.files);
                  }
                }}
                onDrop={(e) => {
                  if (e.dataTransfer.files.length) {
                    e.preventDefault();
                    handleImages(e.dataTransfer.files);
                  }
                }}
                placeholder="# Document&#10;&#10;Write markdown. Paste or drop images to embed them."
                className="w-full flex-1 min-h-0 resize-none bg-surface text-foreground font-mono text-sm border border-border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring"
              />
              <div className="mt-1 text-xs text-muted-foreground">
                {uploading ? (
                  <span className="flex items-center gap-1.5">
                    <Loader2 className="w-3.5 h-3.5 animate-spin" /> Uploading image…
                  </span>
                ) : (
                  "Markdown · paste or drop images to embed"
                )}
              </div>
            </div>
          ) : draftBody ? (
            <MarkdownView source={draftBody} />
          ) : (
            <p className="text-sm text-muted-foreground">This document is empty.</p>
          ))}

        {/* PDF: themed inline viewer (pdf.js) */}
        {doc.kind === "pdf" &&
          (pdfUrl ? (
            <Suspense
              fallback={
                <div className="flex-1 flex items-center justify-center text-muted-foreground">
                  <Loader2 className="w-5 h-5 animate-spin" />
                </div>
              }
            >
              <PdfViewer file={pdfUrl} />
            </Suspense>
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ))}

        {/* Other binary: download only */}
        {doc.kind === "other" && (
          <p className="text-sm text-muted-foreground">
            {doc.filename ?? "File"} — use Download to open it.
          </p>
        )}
        </div>
      </div>
    </div>
  );
}
