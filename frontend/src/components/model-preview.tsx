import { type MouseEvent, type PointerEvent, type ReactNode, type Ref } from "react";
import { ChevronLeft, ChevronRight, FileText, Images } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
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
  expanded,
}: {
  name: string;
  state: PreviewCard;
  expanded: boolean;
}) {
  return state.view ? (
    <div className="h-full w-full" data-library-thumbnail="ready">
      <img
        src={state.view.url}
        alt={name}
        data-preview-view={state.view.id}
        draggable={false}
        className="h-full w-full object-contain"
      />
    </div>
  ) : (
    // The cover keeps the library's reveal, cached reuse and readiness contract.
    <ProtectedThumbnail
      path={state.cover}
      alt={name}
      fade
      previewView="cover"
      className="h-full w-full"
      imageClassName={expanded ? "h-full w-full object-contain" : "h-full w-full object-cover"}
      placeholder={
        <div className="flex h-full w-full items-center justify-center">
          <FileText className="h-10 w-10 text-muted-foreground/40" />
        </div>
      }
    />
  );
}

function ViewIndicator({ state }: { state: PreviewCard }) {
  const orbit = state.views.filter((view) => view.role === "orbit");
  const shown = state.view?.id ?? "cover";
  if (state.views.length < 2) return null;
  return (
    <div
      className="pointer-events-none absolute right-1 bottom-1 rounded bg-card/90 px-2 py-1 text-2xs text-foreground"
      aria-live="polite"
      aria-atomic="true"
    >
      <span>{uiText(labels[shown])}</span>
      {orbit.length >= 2 && (
        <span aria-hidden="true" className="ml-2 inline-flex gap-1">
          {orbit.map((view) => (
            <span
              key={view.id}
              className={`h-1.5 w-1.5 rounded-full ${shown === view.id ? "bg-primary" : "bg-muted-foreground/40"}`}
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
  children,
}: {
  name: string;
  state: PreviewCard;
  /** Viewport admission for this card's primary image. */
  admission: Ref<HTMLDivElement>;
  children?: ReactNode;
}) {
  useUiLocale();
  return (
    <div
      ref={admission}
      className="relative h-48 shrink-0 overflow-hidden border-b border-border bg-muted"
      data-model-preview={name}
      onPointerEnter={state.enter}
      onPointerLeave={state.leave}
    >
      <PreviewImage name={name} state={state} expanded={false} />
      {children}
      <ViewIndicator state={state} />
    </div>
  );
}

const keepOffCard = {
  draggable: false,
  onPointerDown: (event: PointerEvent) => event.stopPropagation(),
  onDragStart: (event: { preventDefault(): void; stopPropagation(): void }) => {
    event.preventDefault();
    event.stopPropagation();
  },
};

/** Arrows, gallery and retry: siblings of the card's link, laid over its image. */
export function ModelPreviewControls({ name, state }: { name: string; state: PreviewCard }) {
  useUiLocale();
  const { preview, views } = state;
  const orbit = views.filter((view) => view.role === "orbit");
  const previous = preview.destination(-1);
  const next = preview.destination(1);
  return (
    <>
      <div
        data-model-preview-controls={name}
        className={cn(
          "pointer-events-none absolute inset-x-0 top-0 z-10 h-48 transition-opacity duration-fast ease-out focus-within:opacity-100",
          state.hovered ? "opacity-100" : "opacity-100 [@media(hover:hover)]:opacity-0",
        )}
      >
        {views.length >= 2 && (
          <>
            {orbit.length >= 2 &&
              [previous, next].map(
                (view, index) =>
                  view && (
                    <Button
                      key={index}
                      {...keepOffCard}
                      variant="secondary"
                      size="icon"
                      className={`pointer-events-auto absolute top-1/2 h-11 w-11 -translate-y-1/2 bg-card/90 ${index === 0 ? "left-1" : "right-1"}`}
                      aria-label={uiText(
                        index === 0 ? "modelPreview.previous" : "modelPreview.next",
                      )}
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
                        <ChevronLeft className="h-5 w-5" />
                      ) : (
                        <ChevronRight className="h-5 w-5" />
                      )}
                    </Button>
                  ),
              )}
            <Button
              {...keepOffCard}
              variant="secondary"
              className="pointer-events-auto absolute bottom-1 left-1 h-11 bg-card/90"
              aria-label={uiText("modelPreview.gallery")}
              onPointerEnter={state.enter}
              onPointerLeave={state.leave}
              onClick={(event) => {
                stop(event);
                state.setOpen(true);
              }}
            >
              <Images className="h-4 w-4" />
              <span>{uiText("modelPreview.views", { count: views.length })}</span>
            </Button>
          </>
        )}
        {preview.failed && (
          <Button
            {...keepOffCard}
            variant="secondary"
            className="pointer-events-auto absolute left-1 top-1 h-11"
            onClick={(event) => {
              stop(event);
              preview.retry();
            }}
          >
            {uiText("modelPreview.retry")}
          </Button>
        )}
      </div>
      {state.open && (
        <Modal open title={uiText("modelPreview.gallery")} onClose={() => state.setOpen(false)}>
          <ModelPreviewGallery name={name} cover={state.cover} previews={state.previews} />
        </Modal>
      )}
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
        className="relative aspect-[4/3] bg-muted"
        data-model-preview={name}
        onPointerEnter={state.enter}
        onPointerLeave={state.leave}
      >
        <PreviewImage name={name} state={state} expanded />
        <ViewIndicator state={state} />
        {preview.failed && (
          <Button
            variant="secondary"
            className="absolute left-1 top-1 h-11"
            onClick={preview.retry}
          >
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
