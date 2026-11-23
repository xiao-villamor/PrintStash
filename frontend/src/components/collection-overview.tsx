import { useRef, useState } from "react";
import { ArrowRight, Box, Folder, Image, Pencil, RotateCw } from "lucide-react";
import { LibraryCardSelection } from "@/components/library-card-selection";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { LibraryPreviewPlaceholder } from "@/components/library-card-placeholder";
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
}) {
  useUiLocale();
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPoint, setMenuPoint] = useState<{ x: number; y: number } | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const hasActions = !selectable && !!(onRename || onEditCover);
  const { ref, pending, models, image, error, retry } = useCollectionOverview(
    collection,
    authorizationRevision,
    onAuthorityRetired,
    previewsReady,
    previewSource,
  );
  const { dragOver, handlers } = useModelDropTarget(collection.path, onDropModel);
  const count = image ? 1 : (models?.length ?? (pending ? 4 : 0));
  const columns = count > 1 ? "grid-cols-2" : "grid-cols-1";
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
      className={`${arriving ? "animate-card-arrive" : ""} group group/selection-card relative min-w-0 overflow-hidden border bg-card transition-[border-color,scale] duration-press ease-out active:scale-[0.99] motion-reduce:active:scale-100 ${
        selected || dragOver ? "border-primary" : "border-border hover:border-border-strong"
      }`}
    >
      <button
        ref={opener}
        type="button"
        aria-keyshortcuts={hasActions ? "Shift+F10" : undefined}
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
        className={`${compact ? "flex items-center gap-3 px-2 py-[7px]" : "block"} w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring`}
      >
        <div
          data-library-preview={
            error
              ? "failed"
              : (collection.model_count > 0 || collection.has_custom_cover) && !models && !image
                ? "pending"
                : "ready"
          }
          className={`collection-card-collage library-preview grid gap-px overflow-hidden ${columns} ${count > 2 ? "grid-rows-2" : "grid-rows-1"} ${compact ? "shrink-0" : ""}`}
          aria-hidden="true"
        >
          {image ? (
            <ProtectedThumbnail
              path={image}
              alt=""
              fade
              priority="background"
              loadingPlaceholder={<LibraryPreviewPlaceholder />}
              placeholder={
                <Folder className={`${compact ? "h-5 w-5" : "h-8 w-8"} text-muted-foreground`} />
              }
              className="flex min-h-0 min-w-0 items-center justify-center overflow-hidden"
              imageClassName="h-full w-full object-cover"
            />
          ) : models && models.length > 0 ? (
            models.map((model, index) => (
              <ProtectedThumbnail
                key={model.key}
                path={model.thumbnail}
                alt=""
                fade
                priority="background"
                loadingPlaceholder={<LibraryPreviewPlaceholder />}
                className={`flex min-h-0 min-w-0 items-center justify-center overflow-hidden ${count === 3 && index === 0 ? "row-span-2" : ""}`}
                imageClassName="h-full w-full object-contain"
                placeholder={
                  <div className="flex h-full w-full items-center justify-center">
                    <Box className={`${compact ? "h-3 w-3" : "h-6 w-6"} text-muted-foreground`} />
                  </div>
                }
              />
            ))
          ) : pending ? (
            Array.from({ length: 4 }, (_, index) => (
              <div key={index} className="library-loading-placeholder min-h-0">
                <LibraryPreviewPlaceholder />
              </div>
            ))
          ) : (
            <div className="col-span-full flex items-center justify-center bg-muted/30">
              <Folder className={`${compact ? "h-5 w-5" : "h-8 w-8"} text-muted-foreground`} />
            </div>
          )}
        </div>
        <div className={`flex min-w-0 flex-1 items-center gap-3 ${compact ? "" : "px-3 py-3"}`}>
          <div className="min-w-0 flex-1">
            <p
              className={`collection-card-name flex min-w-0 items-start gap-1.5 text-sm font-semibold leading-5 text-foreground ${compact ? "" : "min-h-10"}`}
            >
              <Folder className="mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
              <span className={compact ? "truncate" : "line-clamp-2 wrap-anywhere"}>
                {collection.name}
              </span>
            </p>
            <p className={`text-xs text-muted-foreground ${compact ? "" : "mt-1"}`}>
              {uiText("counts.models", { count: collection.model_count })}
            </p>
          </div>
          {!compact && (
            <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
          )}
        </div>
      </button>
      {onToggleSelect && (
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
          className="absolute right-2 top-2 bg-card"
          aria-label={`${uiText("library.collectionOverview.previewRetry")}: ${collection.name}`}
          title={uiText("library.collectionOverview.previewError")}
          onClick={retry}
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden />
        </Button>
      )}
    </div>
  );
  if (!hasActions) return card;
  return (
    <DropdownMenu
      open={menuOpen}
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
