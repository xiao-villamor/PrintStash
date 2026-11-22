import { useState } from "react";
import { useModelPreviews } from "@/lib/use-model-previews";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { coverImageKind, publishedViews, type ModelPreviews } from "@/types/previews";

/**
 * One card's preview state, shared by the image inside the card's link and the
 * controls overlaid beside it: controls are never nested in an anchor, and the
 * card keeps a single link, reading anchor and named image.
 */
export function useModelPreviewCard(
  cover: string | null,
  previews: ModelPreviews,
  size: "thumbnail" | "full" = "thumbnail",
) {
  const { ref, admitted } = useViewportAdmission();
  const views = publishedViews(previews).map((view) =>
    size === "thumbnail" && view.role !== "archive" && view.thumbnail_url
      ? { ...view, url: view.thumbnail_url }
      : view,
  );
  const coverIsLarge = cover !== null && cover === previews.large_cover_url;
  const matchingGenerated =
    previews.kind === "artifact" && (!coverIsLarge || previews.large_cover_view === undefined)
      ? previews.published?.cover_view
      : undefined;
  const matchingArchive = views.find(
    (view) => view.role === "archive" && view.url === previews.large_cover_url,
  )?.id;
  const preview = useModelPreviews(cover, views, admitted, matchingGenerated ?? matchingArchive);
  const [hovered, setHovered] = useState(false);
  const [open, setOpen] = useState(false);
  // A selected view replaces the cover only once its own bytes have decoded.
  const view = preview.selected === "cover" ? null : preview.displayed;
  const image = views.find((candidate) => candidate.id === view?.id);
  const imageKind = image?.image_kind ?? (image ? "render" : coverImageKind(previews, size));
  // Pointer travel between the image and its overlaid controls is one hover.
  function leave(event: { currentTarget: Element; relatedTarget: EventTarget | null }) {
    const next = event.relatedTarget;
    const card = event.currentTarget.closest("article");
    if (
      next instanceof Element &&
      card !== null &&
      next.closest("article") === card &&
      next.closest("[data-model-preview], [data-model-preview-controls]") !== null
    )
      return;
    setHovered(false);
    preview.cancelIntent();
  }
  function enter() {
    if (hovered) return;
    setHovered(true);
    preview.hover();
  }
  const state = {
    views,
    preview,
    view,
    imageKind,
    hovered,
    enter,
    leave,
    open,
    setOpen,
    cover,
    previews,
  };
  return [ref, state] as const;
}
export type PreviewCard = ReturnType<typeof useModelPreviewCard>[1];
