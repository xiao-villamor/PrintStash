/** A disposable worker owns all geometry parsing and transfers its typed arrays. */
import { parseGcode } from "./gcode";
import type { ToolpathWorkerReply } from "./gcode-worker-client";

declare const self: {
  onmessage: ((event: MessageEvent<{ text: string }>) => void) | null;
  postMessage: (message: ToolpathWorkerReply, transfer: Transferable[]) => void;
};

self.onmessage = (event) => {
  try {
    const data = parseGcode(event.data.text);
    self.postMessage({ kind: "ready", data }, [
      data.extrudePositions.buffer,
      data.extrudeColors.buffer,
      data.travelPositions.buffer,
      data.cumulativeVertices.buffer,
    ]);
  } catch (error) {
    self.postMessage(
      {
        kind: "error",
        code:
          error instanceof Error && error.message === "toolpath_segment_limit"
            ? "limit"
            : "invalid",
      },
      [],
    );
  }
};
