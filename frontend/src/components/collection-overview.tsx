import { useLayoutEffect, useRef, useState } from "react";
import { ArrowRight, Folder, Image, Pencil, RotateCw } from "lucide-react";
import { LibraryCardSelection } from "@/components/library-card-selection";
import { CollectionCoverArtwork } from "@/components/collection-cover-artwork";
import { CollectionCardRename } from "@/components/collection-card-editor";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import {
  useCollectionOverview,
  type CollectionPreviewSource,
} from "@/features/library/collection-overview";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { ModelDrag } from "@/lib/model-dnd";
import { useModelDropTarget } from "@/lib/use-model-drop-target";
import type { CollectionRead } from "@/types";

export function CollectionOverviewCard({
  collection,
  compact = false,
  arriving = false,
  authorizationRevision,
  onAuthorityRetired,
  onSelect,
  onIntent,
  onDropModel,
  selectable,
  selected,
  onToggleSelect,
  previewsReady = true,
  previewSource,
  onRename,
  onEditCover,
  renaming = false,
  onRenameEnd,
}: {
  collection: CollectionRead;
  compact?: boolean;
  arriving?: boolean;
  authorizationRevision: string | undefined;
  onAuthorityRetired: () => void;
  onSelect: (path: string, id: number) => void;
  onIntent?: (path: string | null) => void;
  onDropModel?: (source: ModelDrag, path: string) => void;
  selectable?: boolean;
  selected?: boolean;
  onToggleSelect?: (id: number) => void;
  /** False until the page's own Model list has arrived; mosaics read after it. */
  previewsReady?: boolean;
  previewSource?: CollectionPreviewSource;
  onRename?: () => void;
  onEditCover?: () => void;
  renaming?: boolean;
  onRenameEnd?: (saved: boolean) => void;
}) {
  useUiLocale();
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPoint, setMenuPoint] = useState<{ x: number; y: number } | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const hasActions = !selectable && !renaming && !!(onRename || onEditCover);
  const wasRenaming = useRef(false);
  useLayoutEffect(() => {
    if (wasRenaming.current && !renaming) opener.current?.focus();
    wasRenaming.current = renaming;
  }, [renaming]);
  const { ref, pending, models, image, error, retry } = useCollectionOverview(
    collection,
    authorizationRevision,
    onAuthorityRetired,
    previewsReady,
    previewSource,
  );
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  const modelCount = uiText("counts.models", { count: collection.model_count });
  const card = (
    <div
      ref={ref}
      data-collection-path={collection.path}
      data-compact={compact || undefined}
      data-library-anchor={`collection:${collection.id}`}
      {...handlers}
      onContextMenu={
        hasActions
          ? (event) => {
              event.preventDefault();
              setMenuPoint(
                event.clientX || event.clientY ? { x: event.clientX, y: event.clientY } : null,
              );
              setMenuOpen(true);
            }
          : undefined
      }
      className={`${arriving ? "animate-card-arrive" : ""} group group/selection-card relative min-w-0 overflow-hidden border bg-card transition-[border-color,scale] duration-press ease-out ${renaming ? "" : "active:scale-[0.99] motion-reduce:active:scale-100"} ${
        selected || dragOver ? "border-primary" : "border-border hover:border-border-strong"
      }`}
    >
      <button
        ref={opener}
        type="button"
        aria-label={`${collection.name} ${modelCount}`}
        aria-keyshortcuts={hasActions ? "Shift+F10" : undefined}
        disabled={renaming}
        aria-hidden={renaming || undefined}
        onKeyDown={
          hasActions
            ? (event) => {
                if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) {
                  event.preventDefault();
                  setMenuPoint(null);
                  setMenuOpen(true);
                }
              }
            : undefined
        }
        onPointerEnter={() => !selectable && onIntent?.(collection.path)}
        onFocus={() => !selectable && onIntent?.(collection.path)}
        onPointerLeave={() => onIntent?.(null)}
        onBlur={() => onIntent?.(null)}
        onClick={() =>
          selectable ? onToggleSelect?.(collection.id) : onSelect(collection.path, collection.id)
        }
        title={collection.name}
        className="absolute inset-0 z-10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
      />
      <div className={compact ? "flex items-center gap-3 px-2 py-[7px]" : "block"}>
        <CollectionCoverArtwork
          models={models}
          image={image}
          pending={pending}
          compact={compact}
          state={
            error
              ? "failed"
              : (collection.model_count > 0 || collection.has_custom_cover) && !models && !image
                ? "pending"
                : "ready"
          }
          className={compact ? "collection-card-collage shrink-0" : "collection-card-collage"}
        />
        <div className={`flex min-w-0 flex-1 items-center gap-3 ${compact ? "" : "px-3 py-3"}`}>
          <div className="min-w-0 flex-1">
            <div
              className={`collection-card-name flex min-w-0 items-start gap-1.5 text-sm font-semibold leading-5 text-foreground ${compact ? "" : "min-h-10"}`}
            >
              <Folder className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              {renaming && onRenameEnd ? (
                <CollectionCardRename collection={collection} onEnd={onRenameEnd} />
              ) : (
                <span className={compact ? "truncate" : "line-clamp-2 wrap-anywhere"}>
                  {collection.name}
                </span>
              )}
            </div>
            <p className={`text-xs text-muted-foreground ${compact ? "" : "mt-1"}`}>{modelCount}</p>
          </div>
          {!compact && !renaming && (
            <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          )}
        </div>
      </div>
      {onToggleSelect && !renaming && (
        <LibraryCardSelection
          selecting={!!selectable}
          checked={!!selected}
          onChange={() => onToggleSelect(collection.id)}
          label={uiText("Select folder {value1}", { value1: String(collection.name) })}
          className={compact ? "left-auto right-2 top-1/2 -translate-y-1/2" : undefined}
        />
      )}
      {(pending || error || (!image && models && models.length === 0)) && (
        <div className="sr-only" role={error ? "alert" : "status"}>
          {pending
            ? uiText("library.collectionOverview.previewLoading")
            : error
              ? uiText("library.collectionOverview.previewError")
              : uiText("library.collectionOverview.noPreview")}
        </div>
      )}
      {error && (
        <Button
          type="button"
          variant="outline"
          size="icon-sm"
          className="absolute right-2 top-2 z-20 bg-card"
          aria-label={`${uiText("library.collectionOverview.previewRetry")}: ${collection.name}`}
          title={uiText("library.collectionOverview.previewError")}
          onClick={retry}
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden />
        </Button>
      )}
    </div>
  );
  if (!onRename && !onEditCover) return card;
  return (
    <DropdownMenu
      open={hasActions && menuOpen}
      onOpenChange={setMenuOpen}
      trigger={card}
      returnFocusRef={opener}
      align="start"
      anchorPoint={menuPoint}
      portal
      aria-label={uiText("collection.cardActions")}
      contentClassName="w-48 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
    >
      {onRename && (
        <button
          type="button"
          role="menuitem"
          className="library-card-menu-item"
          onClick={() => {
            setMenuOpen(false);
            opener.current?.focus();
            onRename();
          }}
        >
          <Pencil className="size-4 text-muted-foreground" aria-hidden />
          {uiText("Rename")}
        </button>
      )}
      {onEditCover && (
        <button
          type="button"
          role="menuitem"
          className="library-card-menu-item"
          onClick={() => {
            setMenuOpen(false);
            opener.current?.focus();
            onEditCover();
          }}
        >
          <Image className="size-4 text-muted-foreground" aria-hidden />
          {uiText("modelCover.edit")}
        </button>
      )}
    </DropdownMenu>
  );
}
