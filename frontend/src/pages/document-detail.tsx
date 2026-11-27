"use client";

import { translate } from "@/lib/locale";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import {
  type ComponentType,
  Suspense,
  lazy,
  useDeferredValue,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useParams } from "@tanstack/react-router";
import { ArrowLeft, Download, Eye, FileText, Loader2, Pencil } from "lucide-react";

import { printMarkdown } from "@/lib/print-markdown";
import { MarkdownView } from "@/components/markdown-view";
import { getAuthenticatedBlob, uploadDocumentImage } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { useRouter, useSearchParams } from "@/lib/navigation";
import { Link } from "@/lib/link";
import { toast } from "@/lib/toast";
import { useDocument, useDocumentMutations } from "@/lib/queries/documents";
import { Button } from "@/components/ui/button";
import { PageFailure } from "@/components/page-failure";
import { getSessionVersion } from "@/lib/session-transport";
import { ApiError, parseApiError, userMessage } from "@/lib/errors";
import type { DocumentRead } from "@/types";
import NotFound from "./not-found";

// pdf.js is heavy — only pull the chunk in when a PDF is actually opened.
const DefaultPdfViewer = lazy(() =>
  import("@/components/pdf-viewer").then((m) => ({ default: m.PdfViewer })),
);

type ViewMode = "preview" | "edit";

/** The name and body the editor is holding, tagged with the document it belongs to. */
type NewDocument = Omit<DocumentRead, "edit_epoch" | "edit_version"> & {
  edit_epoch: null;
  edit_version: null;
};
type Draft = { docId: number; base: DocumentRead | NewDocument; name: string; body: string };
type SaveAttempt =
  | { kind: "conflict" }
  | { kind: "uncertain"; submitted: { name: string; body: string } };
type SaveRecovery =
  | { docId: number; kind: "access" }
  | (SaveAttempt & { docId: number; latest: "loading" | "ready" | "failed" });
type BinaryPreview = {
  documentId: number;
  kind: DocumentRead["kind"];
  blob: Blob;
  imageUrl: string | null;
};

function canEditDoc(doc: DocumentRead | NewDocument | null, isSuper: boolean): boolean {
  if (isSuper) return true;
  return doc?.effective_role === "edit" || doc?.effective_role === "admin";
}

export default function DocumentDetailPage({
  pdfViewer: PdfViewer = DefaultPdfViewer,
  embeddedDocumentId,
}: {
  pdfViewer?: ComponentType<{ file: Blob }>;
  embeddedDocumentId?: number;
} = {}) {
  const locale = useUiLocale();
  const routeParams = useParams({ strict: false });
  const embedded = embeddedDocumentId !== undefined;
  const id = embedded ? String(embeddedDocumentId) : routeParams.id;
  const isNew = id === "new";
  const docId = Number(id);
  const { user } = useAuth();
  const router = useRouter();
  const searchParams = useSearchParams();
  const collectionParam = searchParams.get("c");
  const cidParam = searchParams.get("cid");
  const collectionId = cidParam ? Number(cidParam) : null;
  const modelParam = searchParams.get("modelId");
  const attachmentModelId = modelParam && /^[1-9]\d*$/.test(modelParam) ? Number(modelParam) : null;
  const startEditing = !embedded && searchParams.get("edit") === "1";

  const invalidId = !isNew && (!id || Number.isNaN(docId));

  // New doc: no DB row yet — it exists only in this render until save POSTs it.
  const newDocument = useMemo<NewDocument>(
    () => ({
      id: 0,
      name: translate(locale, "Untitled document"),
      kind: "markdown",
      collection: collectionParam,
      collection_id: collectionId,
      multipart_model_id: null,
      model_id: attachmentModelId,
      filename: null,
      effective_role: "edit",
      updated_at: "",
      edit_epoch: null,
      edit_version: null,
      body: "",
    }),
    [attachmentModelId, collectionParam, collectionId, locale],
  );

  const documentQuery = useDocument(isNew || invalidId ? null : docId);
  const mutations = useDocumentMutations();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [modeChoice, setModeChoice] = useState<{ docId: number; mode: ViewMode } | null>(null);
  const [recovery, setRecovery] = useState<SaveRecovery | null>(null);
  const routeRef = useRef(id);
  useLayoutEffect(() => {
    routeRef.current = id;
  }, [id]);
  const [savingKey, setSavingKey] = useState<number | null>(null);
  const [uploadingKey, setUploadingKey] = useState<number | null>(null);
  const [binaryPreview, setBinaryPreview] = useState<BinaryPreview | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const printableRef = useRef<HTMLDivElement>(null);
  const [printing, setPrinting] = useState(false);

  // Tagging the fetch results with their document id makes every derived value
  // below reset itself when the route moves to another document, so nothing from
  // the previous one survives into this render.
  const doc = isNew
    ? newDocument
    : (documentQuery.data ?? (draft?.docId === docId ? draft.base : null));
  const notFound =
    invalidId || (documentQuery.error instanceof ApiError && documentQuery.error.status === 404);
  const docKey = isNew ? 0 : docId;
  const saving = savingKey === docKey;
  const uploading = uploadingKey === docKey;
  const liveDraft = draft?.docId === docKey ? draft : null;
  const draftName = liveDraft?.name ?? doc?.name ?? "";
  const draftBody = liveDraft?.body ?? doc?.body ?? "";
  const livePreviewBody = useDeferredValue(draftBody);
  const activeBinaryPreview =
    binaryPreview && binaryPreview.documentId === doc?.id && binaryPreview.kind === doc.kind
      ? binaryPreview
      : null;
  // A new document opens in the editor; anything else opens in preview until the
  // reader asks for one or the other.
  const mode: ViewMode =
    modeChoice?.docId === docKey ? modeChoice.mode : isNew || startEditing ? "edit" : "preview";
  const activeRecovery = recovery?.docId === docKey ? recovery : null;
  const accessDenied =
    activeRecovery?.kind === "access" ||
    (documentQuery.error instanceof ApiError &&
      [401, 403, 404].includes(documentQuery.error.status));

  const canEdit = !accessDenied && canEditDoc(doc, !!user?.is_superuser);
  const backHref =
    doc?.model_id != null
      ? `/models/${doc.model_id}?tab=documentation`
      : doc?.collection
        ? `/?c=${encodeURIComponent(doc.collection)}&v=docs`
        : "/?v=docs";

  function setDraftName(name: string) {
    if (!doc) return;
    setDraft({ docId: docKey, base: liveDraft?.base ?? doc, name, body: draftBody });
  }

  function editDraftBody(next: (current: string) => string) {
    if (!doc) return;
    setDraft((current) => {
      const base =
        current?.docId === docKey
          ? current
          : { docId: docKey, base: doc, name: draftName, body: draftBody };
      return { ...base, body: next(base.body) };
    });
  }

  function setDraftBody(body: string) {
    editDraftBody(() => body);
  }

  const isImage = !!doc?.filename && /\.(png|jpe?g|gif|webp)$/i.test(doc.filename);

  const previewDocumentId = doc?.id;
  const previewDocumentKind = doc?.kind;

  // Fetch protected previews as blobs because an ordinary image/iframe URL
  // cannot carry the API authorization header.
  useEffect(() => {
    if (
      accessDenied ||
      previewDocumentId === undefined ||
      previewDocumentKind === undefined ||
      (previewDocumentKind !== "pdf" && !isImage)
    )
      return;
    const controller = new AbortController();
    let alive = true;
    let url: string | null = null;
    getAuthenticatedBlob(`/api/v1/documents/${previewDocumentId}/file`, controller.signal)
      .then((blob) => {
        if (!alive) return;
        if (isImage) url = URL.createObjectURL(blob);
        setBinaryPreview({
          documentId: previewDocumentId,
          kind: previewDocumentKind,
          blob,
          imageUrl: url,
        });
      })
      .catch(() => alive && toast.error(uiText("Could not load PDF")));
    return () => {
      alive = false;
      controller.abort();
      if (url) URL.revokeObjectURL(url);
      setBinaryPreview((current) => (current?.documentId === previewDocumentId ? null : current));
    };
  }, [previewDocumentId, previewDocumentKind, isImage, accessDenied]);

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
    if (!images.length || !doc || !canEdit || activeRecovery) return;
    if (isNew) {
      toast.error(uiText("Save the document before adding images."));
      return;
    }
    const session = getSessionVersion();
    const route = id;
    setUploadingKey(docKey);
    try {
      for (const file of images) {
        const { url } = await uploadDocumentImage(doc.id, file);
        if (getSessionVersion() !== session || routeRef.current !== route) return;
        insertAtCursor(`\n![${file.name}](${url})\n`);
      }
    } catch (err) {
      if (getSessionVersion() === session && routeRef.current === route) toast.error(err);
    } finally {
      if (getSessionVersion() === session && routeRef.current === route) setUploadingKey(null);
    }
  }

  async function recoverSave(attempt: SaveAttempt) {
    const session = getSessionVersion();
    const route = id;
    setRecovery({ docId: docKey, ...attempt, latest: "loading" });
    const result = await documentQuery.refetch();
    if (getSessionVersion() !== session || routeRef.current !== route) return;
    if (result.error) {
      const denied =
        result.error instanceof ApiError && [401, 403, 404].includes(result.error.status);
      setRecovery(
        denied
          ? { docId: docKey, kind: "access" }
          : { docId: docKey, ...attempt, latest: "failed" },
      );
      return;
    }
    if (
      attempt.kind === "uncertain" &&
      result.data?.name === attempt.submitted.name &&
      result.data.body === attempt.submitted.body
    ) {
      setDraft(null);
      setRecovery(null);
      setModeChoice({ docId: docKey, mode: "preview" });
      toast.success(uiText("documents.saveConfirmed"));
      return;
    }
    setRecovery({ docId: docKey, ...attempt, latest: "ready" });
  }

  async function save() {
    if (!doc || !canEdit || activeRecovery || uploading) return;
    const session = getSessionVersion();
    const route = id;
    const submitted = { name: draftName.trim() || (liveDraft?.base ?? doc).name, body: draftBody };
    setSavingKey(docKey);
    try {
      if (isNew) {
        const created = await mutations.create.mutateAsync({
          session,
          payload: {
            name: draftName.trim() || uiText("Untitled document"),
            collection_id: doc.model_id === null ? collectionId : null,
            model_id: doc.model_id ?? undefined,
            body: draftBody,
          },
        });
        if (getSessionVersion() !== session || routeRef.current !== route) return;
        setModeChoice({ docId: created.id, mode: "edit" });
        router.replace(`/documents/${created.id}`);
        return;
      }
      const base = liveDraft?.base ?? doc;
      if (base.edit_epoch === null || base.edit_version === null)
        throw new Error("Existing document has no editing base");
      await mutations.update.mutateAsync({
        session,
        id: doc.id,
        editVersion: { edit_epoch: base.edit_epoch, edit_version: base.edit_version },
        payload: submitted,
      });
      if (getSessionVersion() !== session || routeRef.current !== route) return;
      setDraft(null);
      setModeChoice({ docId: docKey, mode: "preview" });
    } catch (err) {
      if (getSessionVersion() !== session || routeRef.current !== route) return;
      const error = parseApiError(err);
      if (!isNew && error.status === 412 && error.code === "edit_conflict") {
        await recoverSave({ kind: "conflict" });
      } else if (!isNew && (error.status >= 500 || error.code === "network_unreachable")) {
        await recoverSave({ kind: "uncertain", submitted });
      } else if (!isNew && [401, 403, 404].includes(error.status)) {
        setRecovery({ docId: docKey, kind: "access" });
      } else {
        toast.error(err);
      }
    } finally {
      if (getSessionVersion() === session && routeRef.current === route) setSavingKey(null);
    }
  }

  async function exportPdf() {
    if (!doc || !printableRef.current) return;
    setPrinting(true);
    try {
      await printMarkdown(draftName.trim() || doc.name, printableRef.current);
    } catch (error) {
      toast.error(error);
    } finally {
      setPrinting(false);
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

  if (notFound && !liveDraft) return <NotFound />;
  if (accessDenied && !liveDraft) {
    return (
      <PageFailure
        kind={
          documentQuery.error instanceof ApiError && documentQuery.error.status === 401
            ? "authentication"
            : "forbidden"
        }
        description={userMessage(documentQuery.error)}
      />
    );
  }
  if (documentQuery.isError && !doc && !isNew) {
    return (
      <PageFailure
        kind={
          documentQuery.error instanceof ApiError &&
          documentQuery.error.code === "network_unreachable"
            ? "offline"
            : "unavailable"
        }
        description={userMessage(documentQuery.error)}
        onRetry={() => void documentQuery.refetch()}
        retrying={documentQuery.isFetching}
      />
    );
  }
  if (!doc) {
    return (
      <div
        className={embedded ? "h-full bg-background" : "min-h-screen bg-background"}
        aria-busy="true"
      />
    );
  }

  const isMarkdown = doc.kind === "markdown";
  const editing = isMarkdown && mode === "edit";
  const unsaved =
    isNew ||
    (liveDraft !== null &&
      (draftName !== liveDraft.base.name || draftBody !== (liveDraft.base.body ?? "")));

  return (
    <div className="h-full flex flex-col bg-background">
      {documentQuery.isError && !accessDenied && !activeRecovery && (
        <div role="alert" className="p-4 text-sm text-destructive">
          <p>{userMessage(documentQuery.error)}</p>
          <Button variant="outline" onClick={() => void documentQuery.refetch()}>
            {uiText("Retry")}
          </Button>
        </div>
      )}
      {accessDenied && liveDraft && (
        <div role="alert" className="p-4 text-sm text-destructive">
          {uiText("documents.accessChanged")}
        </div>
      )}
      {activeRecovery && activeRecovery.kind !== "access" && (
        <div role="alert" className="p-4 space-y-3 border-b border-border">
          <p>
            {uiText(
              activeRecovery.kind === "conflict" ? "documents.conflict" : "documents.saveUncertain",
            )}
          </p>
          {activeRecovery.latest === "ready" && documentQuery.data && !accessDenied && (
            <section aria-label={uiText("documents.latestVersion")}>
              <h2 className="font-semibold">{uiText("documents.latestVersion")}</h2>
              <p>{documentQuery.data.name}</p>
              <pre className="whitespace-pre-wrap text-sm max-h-48 overflow-auto">
                {documentQuery.data.body}
              </pre>
              <Button
                variant="outline"
                disabled={!canEdit}
                onClick={() => {
                  const latest = documentQuery.data;
                  if (!latest || !liveDraft) return;
                  setDraft({ ...liveDraft, base: latest });
                  setRecovery(null);
                }}
              >
                {uiText("documents.reviewLatest")}
              </Button>
            </section>
          )}
          {activeRecovery.latest === "loading" && <p aria-busy="true">{uiText("Loading…")}</p>}
          {activeRecovery.latest === "failed" && (
            <div>
              <p>{userMessage(documentQuery.error)}</p>
              <Button variant="outline" onClick={() => void recoverSave(activeRecovery)}>
                {uiText("Retry")}
              </Button>
            </div>
          )}
        </div>
      )}
      <div
        className={
          embedded
            ? "flex min-h-0 flex-1 flex-col overflow-hidden bg-card"
            : `mx-4 mb-4 mt-4 flex min-h-0 flex-1 flex-col overflow-hidden rounded-lg border border-border bg-card sm:mx-6 md:mt-6 ${editing ? "2xl:mx-auto 2xl:w-[calc(100%-3rem)] 2xl:max-w-[96rem]" : "lg:mx-auto lg:w-[calc(100%-3rem)] lg:max-w-5xl"}`
        }
      >
        <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface-container-low px-4 py-3">
          {!embedded && (
            <Link
              href={backHref}
              className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="w-4 h-4" />
              {uiText(doc.model_id === null ? " Back" : "Back to model")}
            </Link>
          )}
          {embedded ? (
            <h3 className="min-w-0 flex-1 break-words text-sm font-semibold">{doc.name}</h3>
          ) : mode === "edit" ? (
            <input
              aria-label={uiText("documents.name")}
              readOnly={accessDenied || !!activeRecovery}
              disabled={saving}
              value={draftName}
              onChange={(e) => setDraftName(e.target.value)}
              className="min-w-40 flex-1 rounded-md border border-transparent bg-field-surface px-2.5 py-1.5 text-base font-semibold text-foreground hover:border-border focus-visible:border-ring focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          ) : (
            <h1 className="min-w-0 flex-1 break-words text-2xl font-semibold leading-tight text-foreground">
              {doc.name}
            </h1>
          )}

          {embedded && isMarkdown && canEdit && (
            <Button variant="outline" size="sm" asChild>
              <Link href={`/documents/${doc.id}?edit=1`}>{uiText(" Edit")}</Link>
            </Button>
          )}
          {!embedded && isMarkdown && (canEdit || mode === "edit") && (
            <div
              role="group"
              aria-label={uiText("Display")}
              className="flex items-center gap-0.5 rounded-md border border-border bg-card p-0.5"
            >
              <Button
                type="button"
                variant="ghost"
                size="xs"
                aria-pressed={mode === "edit"}
                disabled={!canEdit}
                onClick={() => {
                  setDraft(
                    liveDraft ?? { docId: doc.id, base: doc, name: doc.name, body: doc.body ?? "" },
                  );
                  setModeChoice({ docId: docKey, mode: "edit" });
                }}
                className="h-7 px-2 aria-pressed:bg-accent aria-pressed:text-accent-foreground"
              >
                <Pencil className="w-3.5 h-3.5" />
                {uiText(" Edit")}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="xs"
                aria-pressed={mode === "preview"}
                onClick={() => setModeChoice({ docId: docKey, mode: "preview" })}
                className="h-7 px-2 aria-pressed:bg-accent aria-pressed:text-accent-foreground"
              >
                <Eye className="w-3.5 h-3.5" />
                {uiText(" Preview")}
              </Button>
            </div>
          )}
          {editing && (
            <Button
              type="button"
              size="xs"
              onClick={save}
              loading={saving}
              disabled={saving || uploading || !canEdit || !!activeRecovery}
            >
              {uiText("Save")}
            </Button>
          )}
          {isMarkdown && mode === "preview" && !accessDenied && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void exportPdf()}
              loading={printing}
              disabled={printing || !draftBody}
            >
              {uiText("Export as PDF")}
            </Button>
          )}
          {!isMarkdown && !accessDenied && (
            <button
              onClick={downloadFile}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
            >
              <Download className="w-3.5 h-3.5" />
              {uiText(" Download")}
            </button>
          )}
        </header>

        <div
          className={
            editing
              ? "flex min-h-0 flex-1 flex-col"
              : "min-h-0 flex-1 overflow-auto px-4 py-5 pb-24 sm:px-6 md:pb-6"
          }
        >
          {/* Markdown: edit or preview */}
          {isMarkdown &&
            (mode === "edit" ? (
              <>
                <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-2">
                  <section
                    className="flex min-h-0 min-w-0 flex-col bg-background"
                    aria-label={uiText("Markdown source")}
                  >
                    <div className="flex items-center gap-2 border-b border-border bg-card px-4 py-2 text-xs font-medium text-muted-foreground">
                      <FileText className="size-3.5" aria-hidden />
                      {uiText("Markdown source")}
                    </div>
                    <textarea
                      aria-label={uiText("Markdown source")}
                      readOnly={accessDenied || !!activeRecovery}
                      disabled={saving}
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
                      placeholder={uiText(
                        "# Document\n\nWrite markdown. Paste or drop images to embed them.",
                      )}
                      className="min-h-0 w-full flex-1 resize-none bg-field-surface px-5 py-4 font-mono text-sm leading-6 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring sm:px-6"
                    />
                  </section>
                  <section
                    aria-label={uiText("Live preview")}
                    className="hidden min-h-0 min-w-0 flex-col border-s border-border bg-card lg:flex"
                  >
                    <div className="flex items-center gap-2 border-b border-border px-4 py-2 text-xs font-medium text-muted-foreground">
                      <Eye className="size-3.5" aria-hidden />
                      {uiText("Live preview")}
                    </div>
                    <div className="min-h-0 flex-1 overflow-auto px-6 py-5 lg:px-8">
                      {livePreviewBody.trim() ? (
                        <MarkdownView source={livePreviewBody} className="mx-auto max-w-3xl" />
                      ) : (
                        <p className="text-sm text-muted-foreground">
                          {uiText("The preview updates as you write.")}
                        </p>
                      )}
                    </div>
                  </section>
                </div>
                <div className="flex shrink-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-border bg-surface-container-low px-4 py-2 text-xs text-muted-foreground">
                  {uploading ? (
                    <span className="flex items-center gap-1.5">
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      {uiText(" Uploading image…")}
                    </span>
                  ) : (
                    uiText("Markdown · paste or drop images to embed")
                  )}
                  {unsaved && <span>{uiText("Unsaved changes")}</span>}
                </div>
              </>
            ) : draftBody ? (
              <div ref={printableRef} className="mx-auto w-full max-w-3xl px-1 sm:px-4">
                <MarkdownView source={draftBody} />
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{uiText("This document is empty.")}</p>
            ))}

          {/* PDF: themed inline viewer (pdf.js) */}
          {!accessDenied &&
            doc.kind === "pdf" &&
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

          {!accessDenied && doc.kind === "other" && isImage && activeBinaryPreview?.imageUrl && (
            <img
              src={activeBinaryPreview.imageUrl}
              alt={doc.name}
              className="mx-auto max-h-full max-w-full rounded-lg border border-border object-contain"
            />
          )}

          {/* Other binary: download only */}
          {!accessDenied && doc.kind === "other" && !isImage && (
            <p className="text-sm text-muted-foreground">
              {doc.filename ?? uiText("File")}
              {uiText(" — use Download to open it.")}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
