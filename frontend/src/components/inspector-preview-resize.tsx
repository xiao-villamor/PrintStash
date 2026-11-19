import { useRef } from "react";
import { uiText } from "@/lib/locale";
import { MIN_HEIGHT, type useInspectorPreviewHeight } from "./use-inspector-preview-height";

export function InspectorPreviewResize({
  height,
  max,
  resize,
}: ReturnType<typeof useInspectorPreviewHeight>) {
  const drag = useRef<{ pointer: number; y: number; height: number } | null>(null);
  return (
    <div
      role="separator"
      aria-label={uiText("Resize preview")}
      aria-orientation="horizontal"
      aria-valuemin={MIN_HEIGHT}
      aria-valuemax={max}
      aria-valuenow={height}
      tabIndex={0}
      title={uiText("Drag to resize · Double-click to reset")}
      className="group relative z-10 -mt-1 mb-2 h-2 w-full shrink-0 touch-none cursor-row-resize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      onPointerDown={(event) => {
        if (event.button !== 0 || drag.current) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        drag.current = { pointer: event.pointerId, y: event.clientY, height };
      }}
      onPointerMove={(event) => {
        const start = drag.current;
        if (start?.pointer === event.pointerId) resize(start.height + event.clientY - start.y);
      }}
      onLostPointerCapture={(event) => {
        if (drag.current?.pointer === event.pointerId) drag.current = null;
      }}
      onDoubleClick={() => resize(null)}
      onKeyDown={(event) => {
        if (event.key === "ArrowUp") resize(height - 16);
        else if (event.key === "ArrowDown") resize(height + 16);
        else if (event.key === "Home") resize(MIN_HEIGHT);
        else if (event.key === "End") resize(max);
        else return;
        event.preventDefault();
      }}
    >
      <span className="absolute left-1/2 top-1/2 h-1 w-10 -translate-x-1/2 -translate-y-1/2 bg-border group-hover:bg-foreground group-focus-visible:bg-foreground" />
    </div>
  );
}
