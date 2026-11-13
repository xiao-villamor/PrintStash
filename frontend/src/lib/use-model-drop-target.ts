import { useState, type DragEvent } from "react";
import { MODEL_DND_MIME, readModelDrag, type ModelDrag } from "@/lib/model-dnd";

/** Accept internal model moves while leaving operating-system file drops to upload. */
export function useModelDropTarget(
  path: string,
  onDropModel?: (source: ModelDrag, path: string) => void,
) {
  const [dragOver, setDragOver] = useState(false);
  const handlers = {
    onDragOver: (event: DragEvent) => {
      if (!onDropModel || !event.dataTransfer.types.includes(MODEL_DND_MIME)) return;
      event.preventDefault();
      event.stopPropagation();
      event.dataTransfer.dropEffect = "move";
      setDragOver(true);
    },
    onDragLeave: () => setDragOver(false),
    onDrop: (event: DragEvent) => {
      if (!onDropModel || !event.dataTransfer.types.includes(MODEL_DND_MIME)) return;
      event.preventDefault();
      event.stopPropagation();
      setDragOver(false);
      const source = readModelDrag(event.dataTransfer.getData(MODEL_DND_MIME));
      if (source) onDropModel(source, path);
    },
  };
  return { dragOver, handlers };
}
