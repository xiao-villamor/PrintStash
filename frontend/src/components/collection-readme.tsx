"use client";

import { useTaxonomyCommands } from "@/features/library/taxonomy";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useLayoutEffect, useRef, useState } from "react";
import { FileText, Loader2, Pencil } from "lucide-react";

import { useQueryClient } from "@tanstack/react-query";

import { uploadCollectionImage } from "@/lib/api";
import { invalidateCachedAsset } from "@/lib/asset-cache";
import { useCollectionReadme } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { Button } from "@/components/ui/button";
import { MarkdownView } from "@/components/markdown-view";
import { toast } from "@/lib/toast";

export function CollectionReadme({
  collectionId,
  canEdit,
  disabled = false,
  hasReadme,
}: {
  collectionId: number;
  canEdit: boolean;
  /** Keeps the edit affordance in place, but inert, while the library refreshes. */
  disabled?: boolean;
  /** From `CollectionRead.has_readme`; false skips the request entirely. */
  hasReadme: boolean;
}) {
  useUiLocale();
  const queryClient = useQueryClient();
  const { setCollectionReadme } = useTaxonomyCommands();
  // Cached per folder, so revisiting one shows its readme with no request. A
  // readme that cannot be read is treated as absent: the folder is still usable.
  const readmeQuery = useCollectionReadme(collectionId, { enabled: hasReadme });
  const readme = readmeQuery.data ?? null;
  const loading = readmeQuery.isLoading;
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);

  // Collapse tall descriptions so a long/technical one doesn't bury the model
  // list. CLAMP_PX must match the max-h-* class below.
  const CLAMP_PX = 192;
  useLayoutEffect(() => {
    const el = contentRef.current;
    if (!el) return;
    const measure = () => setOverflows(el.scrollHeight > CLAMP_PX);
    // Measure before paint, then track images that change the prose later.
    measure();
    if (!("ResizeObserver" in window)) return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    if (el.firstElementChild) observer.observe(el.firstElementChild);
    return () => observer.disconnect();
  }, [collectionId, readme, editing]);

  // Switching collections drops an open edit in the render that first sees the
  // new id — React's "adjust state when a prop changes". The readme itself is
  // keyed by id in the query cache, so it can never show the previous folder's.
  const [shownCollectionId, setShownCollectionId] = useState(collectionId);
  if (shownCollectionId !== collectionId) {
    setShownCollectionId(collectionId);
    setEditing(false);
    setExpanded(false);
  }
  const [shownReadme, setShownReadme] = useState(readme);
  if (shownReadme !== readme) {
    setShownReadme(readme);
    setExpanded(false);
  }

  function startEdit() {
    setDraft(readme ?? "");
    setEditing(true);
  }

  async function save() {
    setSaving(true);
    try {
      const trimmed = draft.trim();
      const res = await setCollectionReadme(collectionId, trimmed || null);
      queryClient.setQueryData<string | null>(queryKeys.collectionReadme(collectionId), res.readme);
      setEditing(false);
    } catch (err) {
      toast.error(err);
    } finally {
      setSaving(false);
    }
  }

  // Insert markdown at the cursor (or append) — used after an image uploads.
  function insertAtCursor(text: string) {
    const el = textareaRef.current;
    if (!el) {
      setDraft((d) => d + text);
      return;
    }
    const start = el.selectionStart;
    const end = el.selectionEnd;
    setDraft((d) => d.slice(0, start) + text + d.slice(end));
  }

  async function handleFiles(files: FileList | File[]) {
    const images = Array.from(files).filter((f) => f.type.startsWith("image/"));
    if (!images.length) return;
    setUploading(true);
    try {
      for (const file of images) {
        const { url } = await uploadCollectionImage(collectionId, file);
        invalidateCachedAsset(url);
        insertAtCursor(`\n![${file.name}](${url})\n`);
      }
    } catch (err) {
      toast.error(err);
    } finally {
      setUploading(false);
    }
  }

  // Nothing to show and can't edit → render nothing (no empty box).
  if (loading) return null;
  if (!readme && !editing && !canEdit) return null;

  if (editing) {
    return (
      <div className="min-w-0 basis-full space-y-2">
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onPaste={(e) => {
            if (e.clipboardData.files.length) {
              e.preventDefault();
              handleFiles(e.clipboardData.files);
            }
          }}
          onDrop={(e) => {
            if (e.dataTransfer.files.length) {
              e.preventDefault();
              handleFiles(e.dataTransfer.files);
            }
          }}
          rows={8}
          aria-label={uiText("Description")}
          placeholder={uiText(
            "A short description of this collection. Markdown — paste or drop images.",
          )}
          className="w-full bg-surface text-on-surface font-mono text-sm border border-border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Button type="button" size="sm" onClick={save} loading={saving}>
            {uiText("Save")}
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setEditing(false)}
            disabled={saving}
          >
            {uiText("Cancel")}
          </Button>
          {uploading && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
              {uiText(" Uploading image…")}
            </span>
          )}
          <span className="min-w-0 basis-full text-xs text-muted-foreground sm:ml-auto sm:basis-auto">
            {uiText("Markdown · paste or drop images")}
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className={readme ? "min-w-0 basis-full" : "min-w-0"}>
      {readme ? (
        <div>
          <div
            ref={contentRef}
            className={!expanded ? "relative max-h-48 overflow-hidden" : "relative"}
          >
            <MarkdownView source={readme} />
            {overflows && !expanded && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-background to-transparent" />
            )}
          </div>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {overflows && (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={() => setExpanded((value) => !value)}
              >
                {expanded ? uiText("Show less") : uiText("Show more")}
              </Button>
            )}
            {canEdit && (
              <Button
                type="button"
                variant="ghost"
                size="xs"
                onClick={startEdit}
                disabled={disabled}
              >
                <Pencil className="h-3.5 w-3.5" aria-hidden />
                {uiText(" Edit")}
              </Button>
            )}
          </div>
        </div>
      ) : (
        <Button
          type="button"
          variant="ghost"
          size="xs"
          onClick={startEdit}
          disabled={disabled}
          className="h-auto min-h-8 max-w-full justify-start whitespace-normal text-left text-muted-foreground"
        >
          <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {uiText(" Add a description for this collection")}
        </Button>
      )}
    </div>
  );
}
