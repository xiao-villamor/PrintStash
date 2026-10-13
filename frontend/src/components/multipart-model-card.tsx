"use client";

import type { EditingBase } from "@/types/editing";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { LibraryItemLink } from "@/features/library/navigation";
import { useMultipartPublication } from "@/features/library/multipart";
import { useLibraryStar } from "@/features/library/mutations";
import { uiText } from "@/lib/locale";
import { useUiLocale, useI18n } from "@/lib/i18n";
import { Star } from "lucide-react";
import type { TagsReview } from "@/components/entity-tags-editor";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { getMultipartModel, replaceMultipartModelTags } from "@/lib/api";
import type { MultipartModelListItem, TagRead } from "@/types";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { Count, Cover } from "@/components/multipart-model-presentation";
import { multipartError, detailHref } from "@/lib/multipart-model-presentation";

export function MultipartModelCard({
  item,
  origin,
  collectionLabel,
  returnTo,
  availableTags = [],
  onDataChange,
}: {
  item: MultipartModelListItem;
  origin?: LibraryEntry;
  collectionLabel?: string | null;
  returnTo?: string;
  availableTags?: TagRead[];
  onDataChange?: () => void;
}) {
  useUiLocale();
  const { t } = useI18n();
  const starMutation = useLibraryStar();
  const { publish, isCurrent } = useMultipartPublication(item.id);
  const starBusy = starMutation.isPending;
  const starred =
    starMutation.isPending && starMutation.variables
      ? starMutation.variables.starred
      : item.starred;
  const canEditTags = item.effective_role === "edit" || item.effective_role === "admin";

  async function toggleStar() {
    if (starBusy || !isCurrent()) return;
    try {
      await starMutation.mutateAsync({ kind: "multipart", id: item.id, starred: !starred });
    } catch (cause) {
      if (isCurrent()) toast.error(multipartError(cause, t, "multipart.favoriteError"));
    }
  }

  async function saveCardTags(
    nextTags: string[],
    signal: AbortSignal,
    version: EditingBase = item,
  ) {
    if (!isCurrent() || signal.aborted) throw new DOMException("Editor retired", "AbortError");
    const saved = await replaceMultipartModelTags(item.id, nextTags, version);
    if (!(await publish(saved, signal)) || !isCurrent()) return;
    onDataChange?.();
    toast.success(t("multipart.tagsSaved"));
  }

  async function reviewCardTags(signal: AbortSignal): Promise<TagsReview> {
    const latest = await getMultipartModel(item.id, { signal });
    return {
      tags: latest.tags,
      canEdit: latest.effective_role === "edit" || latest.effective_role === "admin",
      save: (tags, commandSignal) => saveCardTags(tags, commandSignal, latest),
      adopt: async (commandSignal) => {
        if (await publish(latest, commandSignal)) onDataChange?.();
      },
    };
  }

  return (
    <article
      className={`${origin ? "" : "animate-card-in"} group relative flex aspect-square h-full min-w-0 max-w-full flex-col overflow-hidden rounded border border-border bg-card text-card-foreground transition-[border-color,box-shadow,transform] duration-fast hover:-translate-y-0.5 hover:border-primary active:scale-[0.99]`}
    >
      <button
        type="button"
        onClick={() => void toggleStar()}
        disabled={starBusy}
        aria-label={
          starred
            ? uiText("Remove {value1} from favorites", { value1: String(item.name) })
            : uiText("Add {value1} to favorites", { value1: String(item.name) })
        }
        className="absolute right-2 top-2 z-10 rounded bg-card/90 p-2 text-muted-foreground shadow-sm transition-[color,background-color,transform] duration-press ease-out hover:bg-card hover:text-primary active:scale-[0.98] disabled:opacity-50"
      >
        <Star className={cn("h-4 w-4", starred && "fill-current text-primary")} />
      </button>
      {canEditTags && (
        <div className="absolute right-2 top-12 z-10">
          <EntityTagsDialog
            entityLabel={item.name}
            tags={item.tags}
            availableTags={availableTags}
            canEdit
            help={t("multipart.tagsHelp")}
            onSave={saveCardTags}
            reviewLatest={reviewCardTags}
            triggerMode="icon"
            triggerClassName="bg-card/90 text-muted-foreground shadow-sm hover:bg-card hover:text-primary"
          />
        </div>
      )}
      <LibraryItemLink
        origin={origin}
        href={detailHref(item.id, returnTo)}
        aria-label={item.name}
        className="flex h-full min-w-0 flex-col overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <div className="relative h-3/5 shrink-0 overflow-hidden border-b border-border bg-muted/40">
          <Cover src={item.cover_thumbnail_url} alt="" />
          <span className="absolute left-2 top-2 max-w-[calc(100%-6rem)] truncate rounded border border-primary/30 bg-card/90 px-2 py-1 font-mono text-3xs font-semibold uppercase tracking-wider text-primary shadow-sm">
            {t("multipart.badge")}
          </span>
        </div>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col p-3">
          <h3
            title={item.name}
            className="line-clamp-2 break-words text-sm font-bold tracking-tight"
          >
            {item.name}
          </h3>
          <p className="mt-1 truncate text-xs text-muted-foreground">
            <Count count={item.part_count} one={t("multipart.part")} many={t("multipart.parts")} />
            {" · "}
            <Count
              count={item.model_count}
              one={t("multipart.model")}
              many={t("multipart.models")}
            />
            {item.guide_count > 0 && (
              <>
                {" · "}
                {item.guide_count}{" "}
                {item.guide_count === 1 ? t("multipart.guide") : t("multipart.guides")}
              </>
            )}
          </p>
          <div className="mt-auto flex min-w-0 items-center gap-1 overflow-hidden border-t border-border pt-2">
            <div className="flex min-w-0 flex-1 gap-1 overflow-hidden">
              {item.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  title={tag}
                  className="max-w-24 truncate rounded border border-primary-soft bg-accent px-1.5 py-0.5 font-mono text-3xs font-semibold tracking-wider text-accent-foreground"
                >
                  {tag}
                </span>
              ))}
              {item.tags.length > 2 && (
                <span className="shrink-0 text-3xs text-muted-foreground">
                  +{item.tags.length - 2}
                </span>
              )}
            </div>
            {collectionLabel && (
              <span
                title={collectionLabel}
                className="max-w-[45%] shrink truncate text-xs text-muted-foreground"
              >
                {collectionLabel}
              </span>
            )}
          </div>
        </div>
      </LibraryItemLink>
    </article>
  );
}
