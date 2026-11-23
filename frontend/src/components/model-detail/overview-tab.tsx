"use client";

import { useId } from "react";
import { MarkdownView } from "@/components/markdown-view";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { ChevronDown, Plus, Trash2, X } from "lucide-react";

import { ModelRead, TagRead } from "@/types";

import { CollectionPicker } from "@/components/collection-picker";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { inputClasses } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import { useComboboxNav } from "@/lib/use-combobox-nav";

import { InspectedPrintCard } from "./inspected-print-card";
import { Localized } from "@/components/ui/localized";

/** Edit-form state owned by the controller (Save lives in the Model editor drawer). */
export type ModelMetaEditor = {
  collection: string;
  collectionLabel: string | null;
  setCollection: (path: string, label: string | null) => void;
  catOpen: boolean;
  setCatOpen: React.Dispatch<React.SetStateAction<boolean>>;
  allowRoot: boolean;
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
  revision,
}: {
  model: ModelRead;
  editing: boolean;
  editor: ModelMetaEditor;
  revision: React.ComponentProps<typeof InspectedPrintCard>;
}) {
  useUiLocale();
  return (
    <Localized>
      <>
        {(revision.file || !model.files.some((file) => file.file_type === "resin")) && (
          <InspectedPrintCard {...revision} />
        )}
        {editing && <ModelMetadataFields editor={editor} />}
      </>
    </Localized>
  );
}

export function ModelMetadataFields({ editor }: { editor: ModelMetaEditor }) {
  useUiLocale();
  const collectionLabelId = useId();
  const descriptionId = useId();
  const sourceUrlId = useId();
  const tagsId = useId();
  const tagShown = editor.filteredTags.slice(0, 6);
  const tagItems = [...tagShown, ...(editor.canCreate ? [editor.tagInput.trim()] : [])];
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
    <Localized>
      <>
        <div className="space-y-4">
          {/* Collection picker */}
          <div>
            <span id={collectionLabelId} className="mb-1.5 block text-sm font-medium">
              {uiText("Collection")}
            </span>
            <DropdownMenu
              open={editor.catOpen}
              onOpenChange={editor.setCatOpen}
              align="start"
              role="dialog"
              contentClassName="w-full bg-surface-container-lowest border border-outline-variant rounded shadow-lg p-2"
              trigger={
                <button
                  type="button"
                  data-menu-trigger
                  onClick={() => editor.setCatOpen((v) => !v)}
                  aria-haspopup="dialog"
                  aria-expanded={editor.catOpen}
                  aria-describedby={collectionLabelId}
                  className={cn(inputClasses, "text-start")}
                >
                  <span className="flex h-full items-center justify-between gap-2">
                    <span
                      className={cn(
                        "min-w-0 truncate",
                        !editor.collection && "text-muted-foreground",
                      )}
                      title={editor.collectionLabel ?? undefined}
                    >
                      {editor.collectionLabel || uiText("None")}
                    </span>
                    <ChevronDown
                      className="size-3.5 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                  </span>
                </button>
              }
            >
              <CollectionPicker
                minRole="edit"
                selectedPath={editor.collection}
                noneLabel={editor.allowRoot ? uiText("None") : undefined}
                emptyLabel={uiText("No editable collections.")}
                onSelect={(collection) => {
                  editor.setCollection(collection?.path ?? "", collection?.display_path ?? null);
                  editor.setCatOpen(false);
                }}
              />
            </DropdownMenu>
          </div>
          {/* Description */}
          <div>
            <label htmlFor={descriptionId} className="mb-1.5 block text-sm font-medium">
              {uiText("Description")}
            </label>
            <textarea
              id={descriptionId}
              aria-describedby={`${descriptionId}-hint`}
              value={editor.description}
              onChange={(e) => editor.setDescription(e.target.value)}
              rows={3}
              className={inputClasses}
              placeholder={uiText("Optional description")}
            />
            <p id={`${descriptionId}-hint`} className="mt-1.5 text-xs text-muted-foreground">
              {uiText("Markdown supported: headings, lists, links and tables.")}
            </p>
            {editor.description.trim() && (
              <details className="mt-2 rounded border border-border p-3">
                <summary className="text-sm font-medium">{uiText("Preview")}</summary>
                <div className="mt-3">
                  <MarkdownView source={editor.description} />
                </div>
              </details>
            )}
          </div>
          {/* Source URL */}
          <div>
            <label htmlFor={sourceUrlId} className="mb-1.5 block text-sm font-medium">
              {uiText("Source URL")}
            </label>
            <input
              id={sourceUrlId}
              type="url"
              value={editor.sourceUrl}
              onChange={(e) => editor.setSourceUrl(e.target.value)}
              className={inputClasses}
              placeholder="https://www.printables.com/model/..."
            />
          </div>
          {/* Tags */}
          <div>
            <label htmlFor={tagsId} className="mb-1.5 block text-sm font-medium">
              {uiText("Model tags")}
            </label>
            <div>
              <input
                id={tagsId}
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
                placeholder={uiText("Search or create — press Enter")}
                className={inputClasses}
              />
              {editor.tagInput && (editor.filteredTags.length > 0 || editor.canCreate) && (
                <div
                  id={tagNav.listboxId}
                  role="listbox"
                  className="mt-1 max-h-40 overflow-y-auto rounded border border-border bg-surface-container-lowest py-1"
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
                        onClick={() => {
                          editor.toggleTag(t.name);
                          editor.setTagInput("");
                        }}
                        className="flex-1 min-w-0 text-left px-3 py-1.5 text-[13px] text-on-surface-variant flex justify-between gap-2"
                      >
                        <span className="truncate">{t.name}</span>
                        <span className="opacity-50">({t.model_count})</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => editor.deleteTag(t)}
                        title={uiText('Delete tag "{value1}"', { value1: String(t.name) })}
                        aria-label={uiText("Delete tag {value1}", { value1: String(t.name) })}
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
                      onClick={() => {
                        editor.createTag(editor.tagInput);
                        editor.setTagInput("");
                      }}
                      className={`w-full text-left px-3 py-1.5 text-[13px] text-primary hover:bg-surface-container-low flex items-center gap-2 ${tagShown.length === tagNav.activeIndex ? "bg-surface-container-low" : ""}`}
                    >
                      <Plus className="size-3 shrink-0" aria-hidden="true" />
                      <span className="min-w-0 wrap-anywhere">
                        {uiText(' Create "')}
                        {editor.tagInput.trim()}
                        {uiText('"')}
                      </span>
                    </button>
                  )}
                </div>
              )}
            </div>
            {editor.tags.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {editor.tags.map((name) => (
                  <span
                    key={name}
                    className="inline-flex items-center gap-1 bg-secondary-container text-on-secondary-container pl-2 pr-1 py-0.5 rounded text-xs"
                  >
                    {name}
                    <button
                      type="button"
                      onClick={() => editor.toggleTag(name)}
                      aria-label={uiText("Remove {value1}", { value1: String(name) })}
                      className="h-3.5 w-3.5 rounded-sm flex items-center justify-center hover:bg-on-secondary-container/10"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </>
    </Localized>
  );
}
