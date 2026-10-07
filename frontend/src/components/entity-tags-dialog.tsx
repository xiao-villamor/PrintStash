"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import type { TagsReview } from "@/components/entity-tags-editor";
import { useState } from "react";
import { Tags } from "lucide-react";

import type { TagRead } from "@/types";
import { Button } from "@/components/ui/button";
import { DeferredDialog } from "@/components/deferred-dialog";
import { lazyImport } from "@/lib/lazy-component";
import { useLibraryStartup } from "@/lib/library-startup-context";

const EntityTagsEditor = lazyImport(() =>
  import("@/components/entity-tags-editor").then((module) => ({
    default: module.EntityTagsEditor,
  })),
);

export function EntityTagsDialog({
  entityLabel,
  tags,
  availableTags,
  canEdit,
  help,
  onSave,
  reviewLatest,
  triggerMode = "inline",
  triggerClassName,
}: {
  entityLabel: string;
  tags: string[];
  availableTags: TagRead[];
  canEdit: boolean;
  help: string;
  onSave: (tags: string[], signal: AbortSignal) => Promise<void>;
  reviewLatest?: (signal: AbortSignal) => Promise<TagsReview>;
  triggerMode?: "inline" | "icon";
  triggerClassName?: string;
}) {
  useUiLocale();
  const [open, setOpen] = useState(false);
  const [session, setSession] = useState(0);
  // The command and the initial selection describe one edit intent, even while
  // its parent receives a newer server snapshot or the lazy editor is loading.
  const [intent, setIntent] = useState({ entityLabel, tags, help, onSave, reviewLatest });
  const startup = useLibraryStartup();

  function openDialog() {
    startup.request("filters");
    setIntent({ entityLabel, tags, help, onSave, reviewLatest });
    setSession((value) => value + 1);
    setOpen(true);
  }

  return (
    <>
      {triggerMode === "icon" ? (
        canEdit ? (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={openDialog}
            aria-label={
              tags.length
                ? uiText("Edit tags for {value1}", { value1: String(entityLabel) })
                : uiText("Add tags to {value1}", { value1: String(entityLabel) })
            }
            title={tags.length ? uiText("Edit tags") : uiText("Add tags")}
            className={triggerClassName}
          >
            <Tags className="h-4 w-4" aria-hidden />
          </Button>
        ) : null
      ) : (
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          {tags.map((tag) => (
            <span
              key={tag}
              className="max-w-48 truncate rounded-full border border-outline-variant bg-surface-container-low px-2 py-0.5 font-mono text-3xs tracking-wider text-on-surface-variant"
              title={tag}
            >
              {tag}
            </span>
          ))}
          {canEdit && (
            <Button type="button" variant="ghost" size="xs" onClick={openDialog}>
              <Tags className="h-3.5 w-3.5" aria-hidden />
              {tags.length ? uiText("Edit tags") : uiText("Add tags")}
            </Button>
          )}
        </div>
      )}

      <DeferredDialog
        open={open && canEdit}
        title={uiText("Tags · {value1}", { value1: String(intent.entityLabel) })}
        onClose={() => setOpen(false)}
      >
        <EntityTagsEditor
          key={session}
          open={open && canEdit}
          entityLabel={intent.entityLabel}
          tags={intent.tags}
          availableTags={availableTags}
          help={intent.help}
          onSave={intent.onSave}
          reviewLatest={intent.reviewLatest}
          onClose={() => setOpen(false)}
        />
      </DeferredDialog>
    </>
  );
}
