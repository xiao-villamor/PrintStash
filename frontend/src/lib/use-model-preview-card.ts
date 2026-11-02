import { useState } from "react";
import { useModelPreviews } from "@/lib/use-model-previews";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { publishedViews, type ModelPreviews } from "@/types/previews";

/**
 * One card's preview state, shared by the image inside the card's link and the
 * controls overlaid beside it: controls are never nested in an anchor, and the
 * card keeps a single link, reading anchor and named image.
 */
export function useModelPreviewCard(cover: string | null, previews: ModelPreviews) {
  const { ref, admitted } = useViewportAdmission();
  const views = publishedViews(previews);
  const preview = useModelPreviews(cover, views, admitted);
  const [hovered, setHovered] = useState(false);
  const [open, setOpen] = useState(false);
  // A selected view replaces the cover only once its own bytes have decoded.
  const view = preview.selected === "cover" ? null : preview.displayed;
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
  const state = { views, preview, view, hovered, enter, leave, open, setOpen, cover, previews };
  return [ref, state] as const;
}
export type PreviewCard = ReturnType<typeof useModelPreviewCard>[1];
