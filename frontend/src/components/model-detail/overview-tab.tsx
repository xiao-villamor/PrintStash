"use client";

import { ChevronDown, ExternalLink, Plus, X } from "lucide-react";

import {
  CollectionRead,
  FileRead,
  FileRevisionUpdate,
  ModelRead,
  TagRead,
} from "@/types";

import { RecommendedPrintCard } from "./recommended-print-card";

/** Edit-form state owned by the controller (Save lives in the page header). */
export type ModelMetaEditor = {
  collection: string;
  setCollection: (v: string) => void;
  catOpen: boolean;
  setCatOpen: React.Dispatch<React.SetStateAction<boolean>>;
  collections: CollectionRead[];
  description: string;
  setDescription: (v: string) => void;
  sourceUrl: string;
  setSourceUrl: (v: string) => void;
  tagInput: string;
  setTagInput: (v: string) => void;
  tags: string[];
  setTags: React.Dispatch<React.SetStateAction<string[]>>;
  toggleTag: (name: string) => void;
  createTag: (name: string) => void;
  filteredTags: TagRead[];
  canCreate: boolean;
};

export function OverviewTab({
  model,
  editing,
  editor,
  recommendedFile,
  hasGcode,
  revisionSaving,
  onSend,
  canSend,
  onCompare,
  onMark,
  onAddRevision,
}: {
  model: ModelRead;
  editing: boolean;
  editor: ModelMetaEditor;
  recommendedFile: FileRead | null;
  hasGcode: boolean;
  revisionSaving: number | null;
  onSend: (fileId: number) => void;
  canSend: boolean;
  onCompare: () => void;
  onMark: (file: FileRead, patch: FileRevisionUpdate) => void;
  onAddRevision: () => void;
}) {
  return (
    <>
      <RecommendedPrintCard
        file={recommendedFile}
        hasGcode={hasGcode}
        saving={revisionSaving}
        onSend={onSend}
        canSend={canSend}
        onCompare={onCompare}
        onMark={onMark}
        onAddRevision={onAddRevision}
      />

      {editing ? (
        <div className="space-y-4">
          {/* Collection picker */}
          <div>
            <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
              Collection
            </label>
            <div className="relative">
              <button
                type="button"
                onClick={() => editor.setCatOpen((v) => !v)}
                className="w-full h-10 flex items-center justify-between bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
              >
                <span className={editor.collection ? "" : "text-[var(--on-surface-variant)]/60"}>
                  {editor.collection || "None"}
                </span>
                <ChevronDown className="h-4 w-4 text-[var(--on-surface-variant)]" />
              </button>
              {editor.catOpen && (
                <>
                  <div className="fixed inset-0 z-40" onClick={() => editor.setCatOpen(false)} />
                  <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-56 overflow-y-auto">
                    <button
                      type="button"
                      onClick={() => { editor.setCollection(""); editor.setCatOpen(false); }}
                      className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                    >
                      None
                    </button>
                    {editor.collections.map((c) => (
                      <button
                        key={c.id}
                        type="button"
                        onClick={() => { editor.setCollection(c.path); editor.setCatOpen(false); }}
                        className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                          editor.collection === c.path
                            ? "text-[var(--primary)] bg-[var(--secondary-container)]"
                            : "text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]"
                        }`}
                      >
                        {c.path} <span className="opacity-50">({c.model_count})</span>
                      </button>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
          {/* Description */}
          <div>
            <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
              Description
            </label>
            <textarea
              value={editor.description}
              onChange={(e) => editor.setDescription(e.target.value)}
              rows={2}
              className="w-full bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent resize-none"
              placeholder="Optional description"
            />
          </div>
          {/* Source URL */}
          <div>
            <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
              Source URL
            </label>
            <input
              type="url"
              value={editor.sourceUrl}
              onChange={(e) => editor.setSourceUrl(e.target.value)}
              className="w-full h-10 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
              placeholder="https://www.printables.com/model/..."
            />
          </div>
          {/* Tags */}
          <div>
            <label className="block font-mono text-[10px] text-[var(--on-surface-variant)] tracking-wider uppercase mb-1.5">
              Tags
            </label>
            <div className="relative">
              <input
                value={editor.tagInput}
                onChange={(e) => editor.setTagInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && editor.tagInput.trim()) {
                    e.preventDefault();
                    editor.createTag(editor.tagInput);
                    editor.setTagInput("");
                  } else if (e.key === "Backspace" && !editor.tagInput && editor.tags.length) {
                    editor.setTags((p) => p.slice(0, -1));
                  }
                }}
                placeholder="Search or create — press Enter"
                className="w-full h-10 bg-[var(--surface)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3 focus:outline-none focus:ring-2 focus:ring-[var(--primary)] focus:border-transparent"
              />
              {editor.tagInput && (editor.filteredTags.length > 0 || editor.canCreate) && (
                <div className="absolute left-0 right-0 top-full mt-1 z-50 bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded shadow-lg py-1 max-h-40 overflow-y-auto">
                  {editor.filteredTags.slice(0, 6).map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      onClick={() => { editor.toggleTag(t.name); editor.setTagInput(""); }}
                      className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] flex justify-between"
                    >
                      <span>{t.name}</span>
                      <span className="opacity-50">({t.model_count})</span>
                    </button>
                  ))}
                  {editor.canCreate && (
                    <button
                      type="button"
                      onClick={() => { editor.createTag(editor.tagInput); editor.setTagInput(""); }}
                      className="w-full text-left px-3 py-1.5 font-mono text-xs text-[var(--primary)] hover:bg-[var(--surface-container-low)] flex items-center gap-2"
                    >
                      <Plus className="h-3 w-3" /> Create &quot;{editor.tagInput.trim()}&quot;
                    </button>
                  )}
                </div>
              )}
            </div>
            {editor.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {editor.tags.map((name) => (
                  <span key={name} className="inline-flex items-center gap-1 bg-[var(--secondary-container)] text-[var(--on-secondary-container)] pl-2 pr-1 py-0.5 rounded font-mono text-[10px] uppercase tracking-wider">
                    {name}
                    <button type="button" onClick={() => editor.toggleTag(name)} aria-label={`Remove ${name}`} className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-[var(--on-secondary-container)]/10">
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          {model.source_url && (
            <a
              href={model.source_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1.5 bg-[var(--surface-container)] text-[var(--on-surface)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider hover:text-[var(--primary)] transition-colors"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Source model
            </a>
          )}
          {model.collection && (
            <span className="bg-[var(--surface-container)] text-[var(--on-surface)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
              {model.collection}
            </span>
          )}
          {model.tags.map((t) => (
            <span key={t} className="bg-[var(--secondary-container)] text-[var(--on-secondary-container)] px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
              {t}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
