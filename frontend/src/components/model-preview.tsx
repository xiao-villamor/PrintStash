import { Fragment, useEffect, useRef, type MouseEvent, type ReactNode, type Ref } from "react";
import { Box, ChevronLeft, ChevronRight, FileText, Images, RotateCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { LibraryPreviewPlaceholder } from "@/components/library-card-placeholder";
import { usePreviewPreferences } from "@/lib/preview-preferences";
import { keepOffCard } from "@/lib/card-overlay";
import { previewTransferPolicy, type PreviewSelection } from "@/lib/use-model-previews";
import { useModelPreviewCard, type PreviewCard } from "@/lib/use-model-preview-card";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { coverImageKind, type ModelPreviews, type PreviewViewId } from "@/types/previews";

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

function previewLabel(state: PreviewCard, id: PreviewSelection) {
  const view =
    id === "cover"
      ? (state.preview.coverView ??
        state.views.find((image) => image.url === state.previews.large_cover_url))
      : state.views.find((image) => image.id === id);
  let label: string | undefined;
  if (view?.role === "archive") {
    label =
      view.plate !== null
        ? uiText("Plate {value1}", { value1: String(view.plate) })
        : view.image_kind === "slicer_render"
          ? uiText("modelPreview.slicerRender")
          : uiText("modelPreview.photo");
  } else {
    const camera = id === "cover" ? (state.previews.large_cover_view ?? view?.id) : view?.id;
    if (camera) label = uiText(labels[camera]);
  }
  return id === "cover"
    ? label
      ? `${label} · ${uiText(labels.cover)}`
      : uiText(labels.cover)
    : (label ?? uiText(labels.cover));
}

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
  const imageClass = cn(
    imageClassName,
    state.imageKind === "slicer_render" && "dark:bg-preview-image-matte",
  );
  return state.view ? (
    <div className={frameClass} data-library-thumbnail="ready">
      <img
        src={state.view.url}
        alt={name}
        data-preview-view={state.view.id}
        draggable={false}
        className={imageClass}
      />
    </div>
  ) : (
    <ProtectedThumbnail
      path={state.cover}
      alt={name}
      fade
      previewView="cover"
      className={frameClass}
      imageClassName={imageClass}
      placeholder={placeholder}
      loadingPlaceholder={loadingPlaceholder}
    />
  );
}

function ViewIndicator({ state, className }: { state: PreviewCard; className?: string }) {
  const { navigationViews } = state.preview;
  const shown = state.view?.id ?? "cover";
  const index = navigationViews.findIndex((view) => view.id === shown);
  if (!state.cover && state.views.length === 0) return null;
  return (
    <div
      className={cn(
        "pointer-events-none flex min-w-0 items-center gap-2 rounded-md bg-card px-2 py-1 text-2xs text-card-foreground dark:bg-card/75",
        className,
      )}
      aria-live="polite"
      aria-atomic="true"
    >
      <span className="library-preview-view-label truncate">{previewLabel(state, shown)}</span>
      {navigationViews.length >= 2 && index >= 0 && (
        <span className="shrink-0 font-mono">
          {index + 1}/{navigationViews.length}
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
      className="library-preview relative h-36 shrink-0 overflow-hidden"
      data-model-preview={name}
      data-image-kind={state.imageKind}
      onPointerEnter={state.enter}
      onPointerLeave={state.leave}
    >
      <PreviewImage
        name={name}
        state={state}
        imageClassName={
          state.imageKind === "photo"
            ? "h-full w-full object-cover object-center"
            : favorites && state.imageKind === "render"
              ? "h-full w-full object-contain"
              : "library-card-thumbnail"
        }
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
              variant="ghost"
              size="icon"
              data-side={index === 0 ? "previous" : "next"}
              className={cn(
                "library-preview-arrow pointer-events-auto absolute top-1/2 -translate-y-1/2",
                "bg-card text-card-foreground shadow-md hover:bg-muted hover:text-foreground focus-visible:bg-card dark:bg-card/90 dark:hover:bg-card dark:focus-visible:bg-card",
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
              title={previewLabel(state, view.id)}
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

/** Image controls overlaid beside the card's link. */
export function ModelPreviewControls({
  name,
  state,
  onRecreate,
  children,
}: {
  name: string;
  state: PreviewCard;
  /** Asks for new views when the published ones cannot be shown. */
  onRecreate?: () => void;
  children: ReactNode;
}) {
  useUiLocale();
  const { showCardPreviewLabel } = usePreviewPreferences();
  const { preview, views } = state;
  const { navigationViews } = preview;
  const shown = state.view?.id ?? "cover";
  const viewLabel = previewLabel(state, shown);
  const viewIndex = navigationViews.findIndex((view) => view.id === shown);
  const showPosition = navigationViews.length >= 2 && viewIndex >= 0;
  return (
    <>
      <div
        data-model-preview-controls={name}
        data-hovered={state.hovered || undefined}
        className="library-preview-controls pointer-events-none absolute inset-x-0 top-0 z-10"
      >
        {navigationViews.length >= 2 && (
          <div
            className="library-preview-reveal library-preview-navigation absolute inset-x-0 top-0 bottom-0"
            data-preview-navigation
          >
            <PreviewArrows state={state} size="card" />
          </div>
        )}
        {(navigationViews.length >= 2 || state.previews.large_cover_url) && (
          <div className="library-preview-reveal absolute bottom-1.5 left-1.5">
            <Button
              {...keepOffCard}
              variant="outline"
              size="xs"
              className="library-preview-views pointer-events-auto h-6 border-input bg-card px-1.5 py-0 text-2xs shadow-md hover:bg-card focus-visible:bg-card"
              aria-label={uiText("modelPreview.gallery")}
              title={
                views.length >= 2
                  ? uiText("modelPreview.views", { count: views.length })
                  : uiText("modelPreview.gallery")
              }
              onPointerEnter={state.enter}
              onPointerLeave={state.leave}
              onClick={(event) => {
                stop(event);
                state.setOpen(true);
              }}
            >
              {showCardPreviewLabel ? (
                <>
                  <span className={showPosition ? "library-preview-view-label" : undefined}>
                    {viewLabel}
                  </span>
                  {showPosition && (
                    <span className="font-mono">
                      {viewIndex + 1}/{navigationViews.length}
                    </span>
                  )}
                </>
              ) : (
                <Images className="size-3.5" aria-hidden />
              )}
            </Button>
          </div>
        )}
        <span className="sr-only" aria-live="polite" aria-atomic="true">
          {state.cover || state.view ? viewLabel : null}
        </span>
        {children}
        {preview.failed && (
          <Button
            {...keepOffCard}
            variant="outline"
            size="icon-sm"
            className="pointer-events-auto absolute right-1.5 top-1.5 h-8 w-8 border-input bg-card shadow-md"
            aria-label={uiText("modelPreview.retry")}
            title={uiText("modelPreview.retry")}
            onClick={(event) => {
              stop(event);
              // The bytes may be gone, not just late: ask for new views, then look again.
              void onRecreate?.();
              preview.retry();
            }}
          >
            <RotateCw className="h-4 w-4" strokeWidth={1.5} aria-hidden />
          </Button>
        )}
      </div>
      <Modal
        open={state.open}
        title={uiText("modelPreview.gallery")}
        description={
          navigationViews.length >= 2
            ? `${name} · ${uiText("modelPreview.views", { count: navigationViews.length })}`
            : name
        }
        onClose={() => state.setOpen(false)}
        className="max-w-6xl"
      >
        <ModelPreviewGallery name={name} cover={state.cover} previews={state.previews} />
      </Modal>
    </>
  );
}

/** Images share the inspector with the mesh and toolpath viewers. */
export function InspectorPreviewImages({
  name,
  cover,
  previews,
}: {
  name: string;
  cover: string | null;
  previews: ModelPreviews;
}) {
  useUiLocale();
  const [admission, state] = useModelPreviewCard(
    previews.large_cover_url ?? cover,
    previews,
    "full",
  );
  return (
    <div
      ref={admission}
      data-inspector-images
      data-image-kind={state.imageKind}
      className="relative h-full w-full overflow-hidden"
    >
      <PreviewImage
        name={name}
        state={state}
        imageClassName={cn(
          "h-full w-full object-center",
          state.imageKind === "photo" ? "object-cover" : "object-contain",
        )}
        loadingPlaceholder={<LibraryPreviewPlaceholder />}
      />
      {state.preview.navigationViews.length >= 2 && (
        <>
          <PreviewArrows state={state} size="gallery" />
          <ViewIndicator
            state={state}
            className="absolute bottom-2 left-1/2 max-w-full -translate-x-1/2 border border-border"
          />
        </>
      )}
      {state.preview.failed && (
        <Button
          variant="outline"
          className="absolute bottom-2 right-2"
          onClick={state.preview.retry}
        >
          {uiText("modelPreview.retry")}
        </Button>
      )}
    </div>
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
  const [admission, state] = useModelPreviewCard(
    previews.large_cover_url ?? cover,
    previews,
    "full",
  );
  const { preview } = state;
  const strip = useRef<HTMLDivElement>(null);
  // The chosen view stays visible in the strip, whichever control chose it.
  useEffect(() => {
    strip.current
      ?.querySelector<HTMLElement>('[aria-pressed="true"]')
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [preview.selected]);
  // Renders and archive photos are different kinds of evidence; the strip says so.
  const firstPhoto = preview.navigationViews.findIndex(
    (view) => view.id !== "cover" && view.role === "archive",
  );
  return (
    <div
      className="model-preview-gallery flex min-h-0 flex-col gap-3"
      onKeyDown={(event) => {
        if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
        if (event.target instanceof HTMLInputElement) return;
        const next = preview.destination(event.key === "ArrowLeft" ? -1 : 1);
        if (!next) return;
        event.preventDefault();
        preview.select(next.id);
      }}
    >
      <div
        ref={admission}
        className="model-preview-gallery-stage relative overflow-hidden bg-muted"
        data-model-preview={name}
        data-image-kind={state.imageKind}
        onPointerEnter={state.enter}
        onPointerLeave={state.leave}
      >
        <PreviewImage name={name} state={state} imageClassName="model-preview-gallery-image" />
        {preview.navigationViews.length >= 2 && <PreviewArrows state={state} size="gallery" />}
        <ViewIndicator
          state={state}
          className="absolute bottom-3 left-1/2 max-w-[calc(100%-6rem)] -translate-x-1/2 border border-border"
        />
        {preview.failed && (
          <Button variant="outline" className="absolute left-2 top-2" onClick={preview.retry}>
            {uiText("modelPreview.retry")}
          </Button>
        )}
      </div>
      {preview.navigationViews.length > 1 && (
        <div className="flex min-w-0 flex-col gap-1.5">
          {!previews.archive_images?.length && (
            <p className="text-xs text-muted-foreground">{uiText("modelPreview.discrete")}</p>
          )}
          <div
            ref={strip}
            role="group"
            className="model-preview-gallery-strip flex gap-1.5 overflow-x-auto pb-1"
            aria-label={uiText("modelPreview.gallery")}
          >
            {preview.navigationViews.map((view, index) => {
              const imageKind =
                view.id === "cover" ? coverImageKind(previews, "full") : view.image_kind;
              const thumbnail =
                view.id === "cover"
                  ? (previews.large_cover_thumbnail_url ??
                    (previews.large_cover_view ? undefined : cover))
                  : view.role === "archive"
                    ? undefined
                    : view.thumbnail_url;
              const path =
                thumbnail ??
                (previewTransferPolicy().neighbor || preview.selected === view.id
                  ? view.url
                  : null);
              const selected = preview.selected === view.id;
              const label = previewLabel(state, view.id);
              return (
                <Fragment key={view.id}>
                  {index === firstPhoto && index > 0 && (
                    <span aria-hidden className="mx-1 w-px shrink-0 self-stretch bg-border" />
                  )}
                  <button
                    type="button"
                    aria-pressed={selected}
                    aria-label={label}
                    title={label}
                    className={cn(
                      "model-preview-gallery-tile flex w-24 shrink-0 flex-col gap-1 border p-1 text-left transition-colors duration-press focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                      selected
                        ? "border-foreground/40 bg-accent text-accent-foreground"
                        : "border-transparent text-muted-foreground hover:bg-muted hover:text-foreground",
                    )}
                    onFocus={() => preview.prioritize(view)}
                    onPointerEnter={() => preview.prioritize(view)}
                    onClick={() => preview.select(view.id)}
                  >
                    <ProtectedThumbnail
                      path={path}
                      alt=""
                      eager
                      fade
                      priority={
                        view.id !== "cover" && view.role === "archive" ? "background" : "intent"
                      }
                      placeholder={<Images className="h-6 w-6 text-muted-foreground" />}
                      loadingPlaceholder={<LibraryPreviewPlaceholder />}
                      className="flex h-16 w-full items-center justify-center overflow-hidden bg-muted"
                      imageClassName={cn(
                        "h-full w-full",
                        view.id !== "cover" && view.role === "archive"
                          ? "object-cover"
                          : "object-contain",
                        imageKind === "slicer_render" && "dark:bg-preview-image-matte",
                      )}
                    />
                    <span className="block w-full truncate text-2xs">{label}</span>
                  </button>
                </Fragment>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
