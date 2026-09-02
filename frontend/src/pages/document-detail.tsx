"use client";

import { type ComponentType, Suspense, lazy, useEffect, useMemo, useRef, useState } from "react";
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
import { useRouter, useSearchParams } from "@/lib/navigation";
import { Link } from "@/lib/link";
import { toast } from "@/lib/toast";
import type { DocumentRead } from "@/types";
import NotFound from "./not-found";

// pdf.js is heavy — only pull the chunk in when a PDF is actually opened.
const DefaultPdfViewer = lazy(() =>
  import("@/components/pdf-viewer").then((m) => ({ default: m.PdfViewer })),
);

const NEW_DOCUMENT_NAME = "Untitled document";

type ViewMode = "preview" | "edit";

/** The name and body the editor is holding, tagged with the document it belongs to. */
type Draft = { docId: number; name: string; body: string };
type BinaryPreview = {
  documentId: number;
  kind: DocumentRead["kind"];
  blob: Blob;
  imageUrl: string | null;
};

function canEditDoc(doc: DocumentRead | null, isSuper: boolean): boolean {
  if (isSuper) return true;
  return doc?.effective_role === "edit" || doc?.effective_role === "admin";
}

export default function DocumentDetailPage({
  pdfViewer: PdfViewer = DefaultPdfViewer,
}: {
  pdfViewer?: ComponentType<{ file: Blob }>;
} = {}) {
  const { id } = useParams();
  const isNew = id === "new";
  const docId = Number(id);
  const { user } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const collectionParam = searchParams.get("c");
  const cidParam = searchParams.get("cid");
  const collectionId = cidParam ? Number(cidParam) : null;

  const invalidId = !isNew && (!id || Number.isNaN(docId));

  // New doc: no DB row yet — it exists only in this render until save POSTs it.
  const newDocument = useMemo<DocumentRead>(
    () => ({
      id: 0,
      name: NEW_DOCUMENT_NAME,
      kind: "markdown",
      collection: collectionParam,
      collection_id: collectionId,
      multipart_model_id: null,
      filename: null,
      effective_role: "edit",
      updated_at: "",
      body: "",
    }),
    [collectionParam, collectionId],
  );

  const [loadedDoc, setLoadedDoc] = useState<DocumentRead | null>(null);
  const [failedDocId, setFailedDocId] = useState<number | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [modeChoice, setModeChoice] = useState<ViewMode | null>(null);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [binaryPreview, setBinaryPreview] = useState<BinaryPreview | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Tagging the fetch results with their document id makes every derived value
  // below reset itself when the route moves to another document, so nothing from
  // the previous one survives into this render.
  const doc = isNew ? newDocument : loadedDoc?.id === docId ? loadedDoc : null;
  const notFound = invalidId || failedDocId === docId;
  const docKey = doc?.id ?? 0;
  const liveDraft = draft?.docId === docKey ? draft : null;
  const draftName = liveDraft?.name ?? doc?.name ?? "";
  const draftBody = liveDraft?.body ?? doc?.body ?? "";
  const activeBinaryPreview =
    binaryPreview && binaryPreview.documentId === doc?.id && binaryPreview.kind === doc.kind
      ? binaryPreview
      : null;
  // A new document opens in the editor; anything else opens in preview until the
  // reader asks for one or the other.
  const mode: ViewMode = modeChoice ?? (isNew ? "edit" : "preview");

  const canEdit = canEditDoc(doc, !!user?.is_superuser);
  const backHref = doc?.collection
    ? `/?c=${encodeURIComponent(doc.collection)}&v=docs`
    : "/?v=docs";

  function setDraftName(name: string) {
    setDraft({ docId: docKey, name, body: draftBody });
  }

  function editDraftBody(next: (current: string) => string) {
    setDraft((current) => {
      const base =
        current?.docId === docKey ? current : { docId: docKey, name: draftName, body: draftBody };
      return { ...base, body: next(base.body) };
    });
  }

  function setDraftBody(body: string) {
    editDraftBody(() => body);
  }

  useEffect(() => {
    if (isNew || invalidId) return;
    let alive = true;
    getDocument(docId)
      .then((d) => {
        if (alive) setLoadedDoc(d);
      })
      .catch(() => {
        if (alive) setFailedDocId(docId);
      });
    return () => {
      alive = false;
    };
  }, [docId, isNew, invalidId]);

  const isImage = !!doc?.filename && /\.(png|jpe?g|gif|webp)$/i.test(doc.filename);

  // Fetch protected previews as blobs because an ordinary image/iframe URL
  // cannot carry the API authorization header.
  useEffect(() => {
    if (!doc || (doc.kind !== "pdf" && !isImage)) return;
    let alive = true;
    let url: string | null = null;
    getAuthenticatedBlob(`/api/v1/documents/${doc.id}/file`)
      .then((blob) => {
        if (!alive) return;
        if (isImage) url = URL.createObjectURL(blob);
        setBinaryPreview({ documentId: doc.id, kind: doc.kind, blob, imageUrl: url });
      })
      .catch(() => alive && toast.error("Could not load PDF"));
    return () => {
      alive = false;
      if (url) URL.revokeObjectURL(url);
    };
  }, [doc, isImage]);

  function insertAtCursor(text: string) {
    const el = textareaRef.current;
    if (!el) {
      editDraftBody((b) => b + text);
      return;
    }
    const { selectionStart: s, selectionEnd: e } = el;
    editDraftBody((b) => b.slice(0, s) + text + b.slice(e));
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
          name: draftName.trim() || NEW_DOCUMENT_NAME,
          collection_id: collectionId,
          body: draftBody,
        });
        // The route now points at a real row; keep the reader in the editor they
        // were already in rather than bouncing them into preview.
        setModeChoice("edit");
        router.replace(`/documents/${created.id}`);
        return;
      }
      const updated = await updateDocument(doc.id, {
        name: draftName.trim() || doc.name,
        body: draftBody,
      });
      setLoadedDoc(updated);
      setModeChoice("preview");
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
  if (!doc) {
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
              onClick={() => setModeChoice("edit")}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
            >
              <Pencil className="w-3.5 h-3.5" /> Edit
            </button>
          )}
          {isMarkdown && mode === "edit" && (
            <>
              <button
                onClick={() => setModeChoice("preview")}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
              >
                <Eye className="w-3.5 h-3.5" /> Preview
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover disabled:opacity-50"
              >
                {saving ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Save className="w-3.5 h-3.5" />
                )}
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
            (activeBinaryPreview ? (
              <Suspense
                fallback={
                  <div className="flex-1 flex items-center justify-center text-muted-foreground">
                    <Loader2 className="w-5 h-5 animate-spin" />
                  </div>
                }
              >
                <PdfViewer file={activeBinaryPreview.blob} />
              </Suspense>
            ) : (
              <div className="flex-1 flex items-center justify-center text-muted-foreground">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            ))}

          {doc.kind === "other" && isImage && activeBinaryPreview?.imageUrl && (
            <img
              src={activeBinaryPreview.imageUrl}
              alt={doc.name}
              className="mx-auto max-h-full max-w-full rounded-lg border border-border object-contain"
            />
          )}

          {/* Other binary: download only */}
          {doc.kind === "other" && !isImage && (
            <p className="text-sm text-muted-foreground">
              {doc.filename ?? "File"} — use Download to open it.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
