"use client";

import { ChevronDown, ExternalLink, Plus, Trash2, X } from "lucide-react";

import {
  CollectionRead,
  FileRead,
  FileRevisionUpdate,
  ModelRead,
  TagRead,
} from "@/types";

import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useComboboxNav } from "@/lib/use-combobox-nav";

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
  deleteTag: (tag: TagRead) => void;
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
  const tagShown = editor.filteredTags.slice(0, 6);
  const tagItems = [
    ...tagShown,
    ...(editor.canCreate ? [editor.tagInput.trim()] : []),
  ];
  const tagNav = useComboboxNav(editor.tagInput ? tagItems.length : 0, {
    onSelect: (i) => {
      if (i < tagShown.length) {
        editor.toggleTag(tagShown[i].name);
      } else {
        editor.createTag(editor.tagInput);
      }
      editor.setTagInput("");
    },
    onCommitInput: () => {
      if (editor.tagInput.trim()) {
        editor.createTag(editor.tagInput);
        editor.setTagInput("");
      }
    },
  });

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
            <label className="block font-mono text-3xs text-on-surface-variant tracking-wider uppercase mb-1.5">
              Collection
            </label>
            <DropdownMenu
              open={editor.catOpen}
              onOpenChange={editor.setCatOpen}
              align="start"
              role="listbox"
              contentClassName="w-full bg-surface-container-lowest border border-outline-variant rounded shadow-lg py-1 max-h-56 overflow-y-auto"
              trigger={
                <button
                  type="button"
                  data-menu-trigger
                  onClick={() => editor.setCatOpen((v) => !v)}
                  aria-haspopup="listbox"
                  aria-expanded={editor.catOpen}
                  className="w-full h-10 flex items-center justify-between bg-surface text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
                >
                  <span className={editor.collection ? "" : "text-on-surface-variant/60"}>
                    {editor.collection || "None"}
                  </span>
                  <ChevronDown className="h-4 w-4 text-on-surface-variant" />
                </button>
              }
            >
              <button
                type="button"
                role="option"
                aria-selected={editor.collection === ""}
                onClick={() => { editor.setCollection(""); editor.setCatOpen(false); }}
                className="w-full text-left px-3 py-1.5 font-mono text-xs text-on-surface-variant hover:bg-surface-container-low"
              >
                None
              </button>
              {editor.collections.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  role="option"
                  aria-selected={editor.collection === c.path}
                  onClick={() => { editor.setCollection(c.path); editor.setCatOpen(false); }}
                  className={`w-full text-left px-3 py-1.5 font-mono text-xs transition-colors ${
                    editor.collection === c.path
                      ? "text-primary bg-secondary-container"
                      : "text-on-surface-variant hover:bg-surface-container-low"
                  }`}
                >
                  {c.path} <span className="opacity-50">({c.model_count})</span>
                </button>
              ))}
            </DropdownMenu>
          </div>
          {/* Description */}
          <div>
            <label className="block font-mono text-3xs text-on-surface-variant tracking-wider uppercase mb-1.5">
              Description
            </label>
            <textarea
              value={editor.description}
              onChange={(e) => editor.setDescription(e.target.value)}
              rows={2}
              className="w-full bg-surface text-on-surface font-mono text-sm border border-outline-variant rounded px-3 py-2 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent resize-none"
              placeholder="Optional description"
            />
          </div>
          {/* Source URL */}
          <div>
            <label className="block font-mono text-3xs text-on-surface-variant tracking-wider uppercase mb-1.5">
              Source URL
            </label>
            <input
              type="url"
              value={editor.sourceUrl}
              onChange={(e) => editor.setSourceUrl(e.target.value)}
              className="w-full h-10 bg-surface text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              placeholder="https://www.printables.com/model/..."
            />
          </div>
          {/* Tags */}
          <div>
            <label className="block font-mono text-3xs text-on-surface-variant tracking-wider uppercase mb-1.5">
              Tags
            </label>
            <div className="relative">
              <input
                value={editor.tagInput}
                onChange={(e) => {
                  editor.setTagInput(e.target.value);
                  tagNav.setActiveIndex(-1);
                }}
                {...tagNav.inputProps}
                onKeyDown={(e) => {
                  tagNav.inputProps.onKeyDown(e);
                  if (e.defaultPrevented) return;
                  if (e.key === "Backspace" && !editor.tagInput && editor.tags.length) {
                    editor.setTags((p) => p.slice(0, -1));
                  }
                }}
                placeholder="Search or create — press Enter"
                className="w-full h-10 bg-surface text-on-surface font-mono text-sm border border-outline-variant rounded px-3 focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent"
              />
              {editor.tagInput && (editor.filteredTags.length > 0 || editor.canCreate) && (
                <div
                  id={tagNav.listboxId}
                  role="listbox"
                  className="pop-in absolute left-0 right-0 top-full mt-1 z-dropdown bg-surface-container-lowest border border-outline-variant rounded shadow-lg py-1 max-h-40 overflow-y-auto"
                >
                  {tagShown.map((t, i) => (
                    <div
                      key={t.id}
                      id={tagNav.optionId(i)}
                      role="option"
                      aria-selected={i === tagNav.activeIndex}
                      className={`group flex items-center hover:bg-surface-container-low ${i === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
                    >
                      <button
                        type="button"
                        onClick={() => { editor.toggleTag(t.name); editor.setTagInput(""); }}
                        className="flex-1 min-w-0 text-left px-3 py-1.5 font-mono text-xs text-on-surface-variant flex justify-between gap-2"
                      >
                        <span className="truncate">{t.name}</span>
                        <span className="opacity-50">({t.model_count})</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => editor.deleteTag(t)}
                        title={`Delete tag "${t.name}"`}
                        aria-label={`Delete tag ${t.name}`}
                        className="px-2 py-1.5 text-on-surface-variant/50 hover:text-error opacity-0 group-hover:opacity-100 focus:opacity-100 transition-opacity"
                      >
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  ))}
                  {editor.canCreate && (
                    <button
                      type="button"
                      id={tagNav.optionId(tagShown.length)}
                      role="option"
                      aria-selected={tagShown.length === tagNav.activeIndex}
                      onClick={() => { editor.createTag(editor.tagInput); editor.setTagInput(""); }}
                      className={`w-full text-left px-3 py-1.5 font-mono text-xs text-primary hover:bg-surface-container-low flex items-center gap-2 ${tagShown.length === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
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
                  <span key={name} className="inline-flex items-center gap-1 bg-secondary-container text-on-secondary-container pl-2 pr-1 py-0.5 rounded font-mono text-3xs uppercase tracking-wider">
                    {name}
                    <button type="button" onClick={() => editor.toggleTag(name)} aria-label={`Remove ${name}`} className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-on-secondary-container/10">
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
              className="inline-flex items-center gap-1.5 bg-surface-container text-on-surface px-3 py-1 rounded font-mono text-xs uppercase tracking-wider hover:text-primary transition-colors"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Source model
            </a>
          )}
          {model.collection && (
            <span className="bg-surface-container text-on-surface px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
              {model.collection}
            </span>
          )}
          {model.tags.map((t) => (
            <span key={t} className="bg-secondary-container text-on-secondary-container px-3 py-1 rounded font-mono text-xs uppercase tracking-wider">
              {t}
            </span>
          ))}
        </div>
      )}
    </>
  );
}
