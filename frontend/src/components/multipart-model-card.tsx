"use client";

import type { LibraryEntry } from "@/features/library/navigation-state";
import { LibraryItemLink } from "@/features/library/navigation";
import { useMultipartPublication } from "@/features/library/multipart";
import { useLibraryStar } from "@/features/library/mutations";
import { uiText } from "@/lib/locale";
import { useUiLocale, useI18n } from "@/lib/i18n";
import { LibraryCardSelection } from "@/components/library-card-selection";
import { Star, Tags } from "lucide-react";
import { MultipartModelTagsDialog } from "@/components/multipart-model-tags-dialog";
import { Button } from "@/components/ui/button";
import type { MultipartModelListItem, TagRead } from "@/types";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { Count, Cover } from "@/components/multipart-model-presentation";
import { multipartError, detailHref } from "@/lib/multipart-model-presentation";

export function MultipartModelCard({
  item,
  origin,
  arriving = false,
  collectionLabel,
  returnTo,
  availableTags = [],
  onDataChange,
  onEditTags,
  selectable = false,
  selected = false,
  onToggleSelect,
  favorites = false,
}: {
  item: MultipartModelListItem;
  origin?: LibraryEntry;
  arriving?: boolean;
  collectionLabel?: string | null;
  returnTo?: string;
  availableTags?: TagRead[];
  onDataChange?: () => void;
  onEditTags?: (item: MultipartModelListItem) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number, range?: boolean) => void;
  favorites?: boolean;
}) {
  useUiLocale();
  const { t } = useI18n();
  const starMutation = useLibraryStar();
  const { isCurrent } = useMultipartPublication(item.id);
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

  return (
    <article
      data-favorites={favorites || undefined}
      className={cn(
        `${origin ? "" : "animate-card-in"} ${arriving ? "animate-card-arrive" : ""} library-model-card group group/selection-card relative flex h-full min-w-0 max-w-full flex-col overflow-hidden rounded border border-border bg-card text-card-foreground transition-[border-color,box-shadow,transform] duration-fast hover:border-primary active:scale-[0.99] `,
        selected && "bg-accent text-accent-foreground",
      )}
    >
      {onToggleSelect && (
        <LibraryCardSelection
          selecting={selectable}
          checked={selected}
          label={uiText("Select {value1}", { value1: item.name })}
          onChange={() => onToggleSelect(item.id)}
        />
      )}
      {!favorites && (
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
      )}
      {canEditTags && (
        <div className="absolute right-2 top-12 z-10">
          {onEditTags ? (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={
                item.tags.length
                  ? uiText("Edit tags for {value1}", { value1: item.name })
                  : uiText("Add tags to {value1}", { value1: item.name })
              }
              title={item.tags.length ? uiText("Edit tags") : uiText("Add tags")}
              onClick={() => onEditTags(item)}
              className="bg-card/90 text-muted-foreground shadow-sm hover:bg-card hover:text-primary"
            >
              <Tags className="h-4 w-4" />
            </Button>
          ) : (
            <MultipartModelTagsDialog
              item={item}
              availableTags={availableTags}
              onDataChange={onDataChange}
            />
          )}
        </div>
      )}
      <LibraryItemLink
        origin={origin}
        href={detailHref(item.id, returnTo)}
        aria-label={item.name}
        onClick={(event) => {
          if (selectable) {
            event.preventDefault();
            onToggleSelect?.(item.id, event.shiftKey);
          }
        }}
        className="flex h-full min-w-0 flex-col overflow-hidden focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      >
        <div className="library-preview relative h-36 shrink-0 overflow-hidden">
          <Cover src={item.cover_thumbnail_url} alt="" />
          <span className="absolute left-3.5 bottom-2.5 max-w-[calc(100%-6rem)] truncate font-mono text-3xs font-semibold uppercase tracking-wider text-muted-foreground">
            {t("multipart.badge")}
          </span>
        </div>
        <div className="flex min-h-40 min-w-0 flex-1 flex-col p-3">
          <h3
            title={item.name}
            className="line-clamp-2 break-words text-sm font-semibold tracking-tight"
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
      {favorites && (
        <div className="flex items-center justify-between gap-2 px-3 pb-3 pt-1">
          <Button variant="outline" size="xs" asChild>
            <LibraryItemLink origin={origin} href={detailHref(item.id, returnTo)}>
              {uiText("Open")}
            </LibraryItemLink>
          </Button>
          <Button
            variant="ghost"
            size="xs"
            disabled={starBusy}
            onClick={() => void toggleStar()}
            aria-label={uiText("Remove {value1} from favorites", { value1: item.name })}
          >
            {uiText("Remove favorite")}
          </Button>
        </div>
      )}
    </article>
  );
}
