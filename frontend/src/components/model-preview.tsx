import { useState, type MouseEvent, type ReactNode } from "react";
import { ChevronLeft, ChevronRight, FileText, Images } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { useModelPreviews, previewTransferPolicy } from "@/lib/use-model-previews";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { publishedViews, type ModelPreviews, type PreviewViewId } from "@/types/previews";

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

export function ModelPreview({
  name,
  cover,
  previews,
  link,
  children,
  expanded = false,
}: {
  name: string;
  cover: string | null;
  previews: ModelPreviews;
  link?: {
    href: string;
    origin?: LibraryEntry;
    onClick: (event: MouseEvent<HTMLAnchorElement>) => void;
    onIntent?: (active: boolean) => void;
  };
  children?: ReactNode;
  expanded?: boolean;
}) {
  useUiLocale();
  const [open, setOpen] = useState(false);
  const { ref, admitted } = useViewportAdmission();
  const views = publishedViews(previews);
  const preview = useModelPreviews(cover, views, admitted);
  const orbit = views.filter((view) => view.role === "orbit");
  const multiple = views.length >= 2;
  const previous = preview.destination(-1);
  const next = preview.destination(1);
  const image = preview.displayed ? (
    <img
      src={preview.displayed.url}
      alt={name}
      data-preview-view={preview.displayed.id}
      draggable={false}
      className="h-full w-full object-contain"
    />
  ) : (
    <div className="flex h-full w-full items-center justify-center">
      <FileText className="h-10 w-10 text-muted-foreground/40" />
    </div>
  );
  return (
    <>
      <div
        ref={ref}
        className={
          expanded
            ? "group/preview relative aspect-[4/3] bg-muted"
            : "group/preview relative h-48 shrink-0 overflow-hidden border-b border-border bg-muted"
        }
        data-model-preview={name}
        data-library-thumbnail={
          !cover && !preview.displayed
            ? "missing"
            : preview.displayed
              ? "ready"
              : preview.failed
                ? "failed"
                : "pending"
        }
        onPointerEnter={preview.hover}
        onPointerLeave={preview.cancelIntent}
      >
        {link ? (
          <LibraryItemLink
            aria-label={uiText("Open model")}
            href={link.href}
            origin={link.origin}
            draggable={false}
            onClick={link.onClick}
            onFocus={() => link.onIntent?.(true)}
            onBlur={() => link.onIntent?.(false)}
            className="block h-full w-full"
          >
            {image}
          </LibraryItemLink>
        ) : (
          image
        )}
        {children}
        {multiple && !expanded && (
          <div className="pointer-events-none absolute inset-0 opacity-100 transition-opacity duration-fast ease-out [@media(hover:hover)]:opacity-0 group-hover/preview:opacity-100 group-focus-within/preview:opacity-100">
            {orbit.length >= 2 &&
              [previous, next].map(
                (view, index) =>
                  view && (
                    <Button
                      key={index}
                      variant="secondary"
                      size="icon"
                      draggable={false}
                      className={`pointer-events-auto absolute top-1/2 h-11 w-11 -translate-y-1/2 bg-card/90 ${index === 0 ? "left-1" : "right-1"}`}
                      aria-label={uiText(
                        index === 0 ? "modelPreview.previous" : "modelPreview.next",
                      )}
                      title={uiText(labels[view.id])}
                      onPointerDown={(event) => event.stopPropagation()}
                      onDragStart={(event) => {
                        event.preventDefault();
                        event.stopPropagation();
                      }}
                      onPointerEnter={() => preview.prioritize(view)}
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
              variant="secondary"
              className="pointer-events-auto absolute bottom-1 left-1 h-11 bg-card/90"
              draggable={false}
              aria-label={uiText("modelPreview.gallery")}
              onPointerDown={(event) => event.stopPropagation()}
              onDragStart={(event) => {
                event.preventDefault();
                event.stopPropagation();
              }}
              onClick={(event) => {
                stop(event);
                setOpen(true);
              }}
            >
              <Images className="h-4 w-4" />
              <span>{uiText("modelPreview.views", { count: views.length })}</span>
            </Button>
          </div>
        )}
        {preview.displayed && multiple && (
          <div
            className="pointer-events-none absolute right-1 bottom-1 rounded bg-card/90 px-2 py-1 text-2xs text-foreground"
            aria-live="polite"
            aria-atomic="true"
          >
            <span>{uiText(labels[preview.displayed.id])}</span>
            {orbit.length >= 2 && (
              <span aria-hidden="true" className="ml-2 inline-flex gap-1">
                {orbit.map((view) => (
                  <span
                    key={view.id}
                    className={`h-1.5 w-1.5 rounded-full ${preview.displayed?.id === view.id ? "bg-primary" : "bg-muted-foreground/40"}`}
                  />
                ))}
              </span>
            )}
          </div>
        )}
        {preview.failed && (
          <Button
            variant="secondary"
            className="absolute left-1 top-1 h-11"
            onClick={(event) => {
              stop(event);
              preview.retry();
            }}
          >
            {uiText("modelPreview.retry")}
          </Button>
        )}
      </div>
      {expanded && (
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
                    previewTransferPolicy().neighbor || preview.selected === view.id
                      ? view.url
                      : null
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
      )}
      {open && (
        <Modal open title={uiText("modelPreview.gallery")} onClose={() => setOpen(false)}>
          <ModelPreview name={name} cover={cover} previews={previews} expanded />
        </Modal>
      )}
    </>
  );
}
