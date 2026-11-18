import type { PointerEvent } from "react";

/** Keeps a control laid over a card from pressing or dragging that card. */
export const keepOffCard = {
  draggable: false,
  onPointerDown: (event: PointerEvent) => event.stopPropagation(),
  onDragStart: (event: { preventDefault(): void; stopPropagation(): void }) => {
    event.preventDefault();
    event.stopPropagation();
  },
};
