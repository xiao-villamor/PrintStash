import { type MouseEvent, type ReactNode, type Ref } from "react";
import { Box, ChevronLeft, ChevronRight, FileText, Images } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { LibraryPreviewPlaceholder } from "@/components/library-card-placeholder";
import { keepOffCard } from "@/lib/card-overlay";
import { previewTransferPolicy } from "@/lib/use-model-previews";
import { useModelPreviewCard, type PreviewCard } from "@/lib/use-model-preview-card";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import type { ModelPreviews, PreviewViewId } from "@/types/previews";

const labels = {
  cover: "modelPreview.cover",
  "orbit-0": "modelPreview.orbit0",
  "orbit-1": "modelPreview.orbit1",
  "orbit-2": "modelPreview.orbit2",
  "orbit-3": "modelPreview.orbit3",
  top: "modelPreview.top",
  bottom: "modelPreview.bottom",
  face: "modelPreview.face",
  reverse: "modelPreview.reverse",
  edge: "modelPreview.edge",
} as const satisfies Record<PreviewViewId | "cover", string>;

function stop(event: MouseEvent) {
  event.preventDefault();
  event.stopPropagation();
}

function PreviewImage({
  name,
  state,
  imageClassName,
  placeholder = (
    <div className="flex h-full w-full items-center justify-center">
      <FileText className="h-10 w-10 text-muted-foreground/40" />
    </div>
  ),
  loadingPlaceholder,
}: {
  name: string;
  state: PreviewCard;
  imageClassName: string;
  placeholder?: ReactNode;
  loadingPlaceholder?: ReactNode;
}) {
  const frameClass = "flex h-full w-full items-center justify-center";
  return state.view ? (
    <div className={frameClass} data-library-thumbnail="ready">
      <img
        src={state.view.url}
        alt={name}
        data-preview-view={state.view.id}
        draggable={false}
        className={imageClassName}
      />
    </div>
  ) : (
    <ProtectedThumbnail
      path={state.cover}
      alt={name}
      fade
      previewView="cover"
      className={frameClass}
      imageClassName={imageClassName}
      placeholder={placeholder}
      loadingPlaceholder={loadingPlaceholder}
    />
  );
}

function ViewIndicator({ state, className }: { state: PreviewCard; className?: string }) {
  const { navigationViews } = state.preview;
  const shown = state.view?.id ?? "cover";
  if (!state.cover && state.views.length === 0) return null;
  return (
    <div
      className={cn(
        "pointer-events-none flex min-w-0 items-center gap-2 bg-card px-2 py-1 text-2xs text-card-foreground",
        className,
      )}
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="truncate">{uiText(labels[shown])}</span>
      {navigationViews.length >= 2 && (
        <span aria-hidden="true" className="inline-flex shrink-0 gap-1">
          {navigationViews.map((view) => (
            <span
              key={view.id}
              className={`h-1.5 w-1.5 rounded-full ${shown === view.id ? "bg-foreground" : "bg-muted-foreground/40"}`}
            />
          ))}
        </span>
      )}
    </div>
  );
}

/** The card's image area; it belongs inside the card's own link. */
export function ModelPreviewFrame({
  name,
  state,
  admission,
  favorites,
  children,
}: {
  name: string;
  state: PreviewCard;
  /** Viewport admission for this card's primary image. */
  admission: Ref<HTMLDivElement>;
  favorites: boolean;
  children?: ReactNode;
}) {
  useUiLocale();
  return (
    <div
      ref={admission}
      className="library-preview relative h-36 shrink-0 overflow-hidden pb-11"
      data-model-preview={name}
      onPointerEnter={state.enter}
      onPointerLeave={state.leave}
    >
      <PreviewImage
        name={name}
        state={state}
        imageClassName={favorites ? "w-full h-full object-contain" : "library-card-thumbnail"}
        loadingPlaceholder={<LibraryPreviewPlaceholder />}
        placeholder={
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 px-3 text-center">
            <Box className="h-6 w-6 text-muted-foreground" />
            <span className="text-xs text-muted-foreground">
              {uiText("library.previewMissing")}
            </span>
          </div>
        }
      />
      {children}
    </div>
  );
}

/** Previous and next view, centered on the two edges of the nearest positioned frame. */
function PreviewArrows({ state, size }: { state: PreviewCard; size: "card" | "gallery" }) {
  const { preview } = state;
  const iconClass = size === "card" ? "h-4 w-4" : "h-5 w-5";
  return (
    <>
      {[preview.destination(-1), preview.destination(1)].map(
        (view, index) =>
          view && (
            <Button
              key={index}
              {...keepOffCard}
              variant="outline"
              size="icon"
              data-side={index === 0 ? "previous" : "next"}
              className={cn(
                "library-preview-arrow pointer-events-auto absolute top-1/2 -translate-y-1/2",
                size === "card" ? "h-8 w-6" : "h-11 w-8",
                index === 0
                  ? size === "card"
                    ? "left-1"
                    : "left-2"
                  : size === "card"
                    ? "right-1"
                    : "right-2",
              )}
              aria-label={uiText(index === 0 ? "modelPreview.previous" : "modelPreview.next")}
              title={uiText(labels[view.id])}
              onPointerEnter={() => {
                state.enter();
                preview.prioritize(view);
              }}
              onPointerLeave={state.leave}
              onFocus={() => preview.prioritize(view)}
              onClick={(event) => {
                stop(event);
                preview.select(view.id);
              }}
            >
              {index === 0 ? (
                <ChevronLeft className={iconClass} strokeWidth={1.5} />
              ) : (
                <ChevronRight className={iconClass} strokeWidth={1.5} />
              )}
            </Button>
          ),
      )}
    </>
  );
}

/** Arrows, gallery and retry: siblings of the card's link, laid over its image. */
export function ModelPreviewControls({ name, state }: { name: string; state: PreviewCard }) {
  useUiLocale();
  const { preview, views } = state;
  const { navigationViews } = preview;
  return (
    <>
      <div
        data-model-preview-controls={name}
        data-hovered={state.hovered || undefined}
        className="library-preview-controls pointer-events-none absolute inset-x-0 top-0 z-10"
      >
        {views.length >= 2 && navigationViews.length >= 2 && (
          <div
            className="library-preview-navigation absolute inset-x-0 top-0 bottom-11"
            data-preview-navigation
          >
            <PreviewArrows state={state} size="card" />
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 flex h-11 items-center border-t border-border bg-card text-card-foreground">
          {views.length >= 2 && (
            <Button
              {...keepOffCard}
              variant="outline"
              className="pointer-events-auto h-11 shrink-0 border-0 border-r px-2.5 text-xs"
              aria-label={uiText("modelPreview.gallery")}
              onPointerEnter={state.enter}
              onPointerLeave={state.leave}
              onClick={(event) => {
                stop(event);
                state.setOpen(true);
              }}
            >
              <span>{uiText("modelPreview.views", { count: views.length })}</span>
            </Button>
          )}
          <ViewIndicator state={state} className="flex-1 justify-end" />
        </div>
        {preview.failed && (
          <Button
            {...keepOffCard}
            variant="outline"
            className="pointer-events-auto absolute right-1 top-1 h-11"
            onClick={(event) => {
              stop(event);
              preview.retry();
            }}
          >
            {uiText("modelPreview.retry")}
          </Button>
        )}
      </div>
      <Modal
        open={state.open}
        title={uiText("modelPreview.gallery")}
        onClose={() => state.setOpen(false)}
        className="max-w-6xl"
        bodyClassName="space-y-4"
      >
        <ModelPreviewGallery name={name} cover={state.cover} previews={state.previews} />
      </Modal>
    </>
  );
}

/** Expanded quick preview: the large image plus a directly selectable strip. */
export function ModelPreviewGallery({
  name,
  cover,
  previews,
}: {
  name: string;
  cover: string | null;
  previews: ModelPreviews;
}) {
  useUiLocale();
  const [admission, state] = useModelPreviewCard(cover, previews);
  const { preview, views } = state;
  return (
    <>
      <div
        ref={admission}
        className="model-preview-gallery-stage relative bg-muted"
        data-model-preview={name}
        onPointerEnter={state.enter}
        onPointerLeave={state.leave}
      >
        <PreviewImage name={name} state={state} imageClassName="h-full w-full object-contain" />
        {views.length >= 2 && preview.navigationViews.length >= 2 && (
          <PreviewArrows state={state} size="gallery" />
        )}
        <ViewIndicator
          state={state}
          className="absolute bottom-2 left-1/2 max-w-full -translate-x-1/2 border border-border"
        />
        {preview.failed && (
          <Button variant="outline" className="absolute left-1 top-1 h-11" onClick={preview.retry}>
            {uiText("modelPreview.retry")}
          </Button>
        )}
      </div>
      <div className="space-y-2">
        <p className="text-sm text-muted-foreground">{uiText("modelPreview.discrete")}</p>
        <div
          className="flex gap-2 overflow-x-auto pb-2"
          aria-label={uiText("modelPreview.gallery")}
        >
          {[...(cover ? [{ id: "cover" as const, url: cover }] : []), ...views].map((view) => (
            <Button
              key={view.id}
              variant="ghost"
              aria-pressed={preview.selected === view.id}
              className={`h-auto min-w-24 flex-col p-2 ${preview.selected === view.id ? "bg-accent text-accent-foreground" : ""}`}
              onFocus={() => preview.prioritize(views.find((image) => image.id === view.id))}
              onClick={() => preview.select(view.id)}
            >
              <ProtectedThumbnail
                path={
                  previewTransferPolicy().neighbor || preview.selected === view.id ? view.url : null
                }
                alt=""
                placeholder={<Images className="h-8 w-8" />}
                className="h-16 w-20"
                imageClassName="h-full w-full object-contain"
              />
              <span>{uiText(labels[view.id])}</span>
            </Button>
          ))}
        </div>
      </div>
    </>
  );
}
