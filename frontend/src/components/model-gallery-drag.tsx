import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  DndContext,
  DragOverlay,
  MouseSensor,
  pointerWithin,
  useSensor,
  useSensors,
  type DragEndEvent,
  type DragStartEvent,
  type Modifier,
} from "@dnd-kit/core";
import { Box } from "lucide-react";
import { captureModelDrag, type ModelDrag } from "@/lib/model-dnd";
import { displayModelName } from "@/lib/display-name";
import type { ModelListItem } from "@/types";

export type GalleryDragSource = { model: ModelListItem; clearHover: () => void };
export type GalleryDropTarget = { collectionPath: string };

const besidePointer: Modifier = ({ activatorEvent, activeNodeRect, transform }) => {
  if (!(activatorEvent instanceof MouseEvent) || !activeNodeRect) return transform;
  return {
    ...transform,
    x: transform.x + activatorEvent.clientX - activeNodeRect.left + 12,
    y: transform.y + activatorEvent.clientY - activeNodeRect.top + 16,
  };
};

/** Keep gallery feedback inside the page while leaving file drops native. */
export function ModelGalleryDrag({
  children,
  onMove,
}: {
  children: ReactNode;
  onMove: (source: ModelDrag, path: string) => void | Promise<void>;
}) {
  const sensors = useSensors(useSensor(MouseSensor, { activationConstraint: { distance: 6 } }));
  const draggedLink = useRef<HTMLAnchorElement | null>(null);
  const [active, setActive] = useState<{
    source: ModelDrag;
    image: string | null;
    clearHover: () => void;
  } | null>(null);

  useEffect(() => {
    function clearDraggedLink() {
      draggedLink.current = null;
    }

    function preventDragClick(event: MouseEvent) {
      if (!event.detail) return;
      const link = draggedLink.current;
      clearDraggedLink();
      // dnd-kit stops propagation, but a released anchor can still navigate.
      if (event.target instanceof Node && link?.contains(event.target)) event.preventDefault();
    }

    document.addEventListener("mousedown", clearDraggedLink, true);
    document.addEventListener("click", preventDragClick, true);
    return () => {
      document.removeEventListener("mousedown", clearDraggedLink, true);
      document.removeEventListener("click", preventDragClick, true);
    };
  }, []);

  function onDragStart(event: DragStartEvent) {
    // SAFETY: Only ModelCard registers drags in this context with GalleryDragSource.
    const data = event.active.data.current as GalleryDragSource | undefined;
    if (!data) return;
    const element = event.activatorEvent.target;
    draggedLink.current = element instanceof Element ? element.closest("a") : null;
    const image =
      element instanceof Element
        ? element.closest("article")?.querySelector<HTMLImageElement>("[data-model-preview] img")
        : null;
    data.clearHover();
    setActive({
      source: captureModelDrag(data.model),
      image: image?.complete && image.naturalWidth > 0 ? image.currentSrc : null,
      clearHover: data.clearHover,
    });
  }

  function clearDrag() {
    active?.clearHover();
    setActive(null);
  }

  function onDragEnd(event: DragEndEvent) {
    // SAFETY: CollectionOverviewCard is this context's only drop target.
    const target = event.over?.data.current as GalleryDropTarget | undefined;
    if (active && target && target.collectionPath !== active.source.collection)
      void onMove(active.source, target.collectionPath);
    clearDrag();
  }

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={pointerWithin}
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      onDragCancel={clearDrag}
    >
      {children}
      {createPortal(
        <DragOverlay
          dropAnimation={null}
          modifiers={[besidePointer]}
          className="pointer-events-none"
        >
          {active && (
            <div
              aria-hidden
              data-gallery-drag-preview=""
              className="flex h-12 w-fit max-w-60 items-center gap-2 rounded-md border border-border-strong bg-popover px-2 text-sm font-medium text-popover-foreground shadow-lg"
            >
              {active.image ? (
                <img
                  src={active.image}
                  alt=""
                  draggable={false}
                  className="size-8 shrink-0 rounded bg-muted object-contain"
                />
              ) : (
                <Box className="size-4 shrink-0 text-muted-foreground" />
              )}
              <span className="min-w-0 truncate">{displayModelName(active.source.name)}</span>
            </div>
          )}
        </DragOverlay>,
        document.body,
      )}
    </DndContext>
  );
}
