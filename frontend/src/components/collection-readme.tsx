"use client";

import { useEffect, useRef, useState } from "react";
import { FileText, Loader2, Pencil } from "lucide-react";

import { getCollectionReadme, setCollectionReadme, uploadCollectionImage } from "@/lib/api";
import { invalidateCachedAsset } from "@/lib/asset-cache";
import { MarkdownView } from "@/components/markdown-view";
import { toast } from "@/lib/toast";

export function CollectionReadme({
  collectionId,
  canEdit,
}: {
  collectionId: number;
  canEdit: boolean;
}) {
  const [readme, setReadme] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
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
  useEffect(() => {
    const el = contentRef.current;
    setOverflows(!!el && el.scrollHeight > CLAMP_PX + 8);
    setExpanded(false);
  }, [readme]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    setEditing(false);
    getCollectionReadme(collectionId)
      .then((r) => alive && setReadme(r.readme))
      .catch(() => alive && setReadme(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [collectionId]);

  function startEdit() {
    setDraft(readme ?? "");
    setEditing(true);
  }

  async function save() {
    setSaving(true);
    try {
      const trimmed = draft.trim();
      const res = await setCollectionReadme(collectionId, trimmed || null);
      setReadme(res.readme);
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
      <div className="px-4 sm:px-6 py-4 bg-background border-b border-border">
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
          placeholder="A short description of this collection. Markdown — paste or drop images."
          className="w-full bg-surface text-on-surface font-mono text-sm border border-border rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <div className="flex items-center gap-2 mt-2">
          <button
            onClick={save}
            disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-primary-foreground bg-primary rounded hover:bg-primary-hover disabled:opacity-50"
          >
            {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Save
          </button>
          <button
            onClick={() => setEditing(false)}
            disabled={saving}
            className="px-3 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted"
          >
            Cancel
          </button>
          {uploading && (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Uploading image…
            </span>
          )}
          <span className="ml-auto text-xs text-muted-foreground">
            Markdown · paste or drop images
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="px-4 sm:px-6 py-4 bg-background border-b border-border">
      {readme ? (
        <div className="relative group/readme">
          <div
            ref={contentRef}
            className={!expanded && overflows ? "max-h-48 overflow-hidden" : ""}
          >
            <MarkdownView source={readme} />
          </div>
          {overflows && (
            <>
              {!expanded && (
                <div className="pointer-events-none absolute inset-x-0 bottom-7 h-12 bg-gradient-to-t from-background to-transparent" />
              )}
              <button
                onClick={() => setExpanded((v) => !v)}
                className="mt-1 text-xs font-medium text-primary hover:underline"
              >
                {expanded ? "Show less" : "Show more"}
              </button>
            </>
          )}
          {canEdit && (
            <button
              onClick={startEdit}
              className="absolute top-0 right-0 flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-foreground bg-background border border-border rounded hover:bg-muted opacity-0 group-hover/readme:opacity-100 transition-opacity"
            >
              <Pencil className="w-3.5 h-3.5" /> Edit
            </button>
          )}
        </div>
      ) : (
        <button
          onClick={startEdit}
          className="flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground"
        >
          <FileText className="w-4 h-4" /> Add a description for this collection
        </button>
      )}
    </div>
  );
}
