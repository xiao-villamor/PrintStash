/** Shared replies keep the worker independent of its browser-side controller. */
import type { ToolpathData } from "./gcode";

export type ToolpathWorkerReply =
  | { kind: "ready"; data: ToolpathData }
  | { kind: "error"; code: "limit" | "invalid" };
