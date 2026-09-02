import type { BrowserCaptureMessage } from "./capture-adapter.ts";

export type BrowserCaptureRoute = "candidate_confirmation" | "manual_file" | "legacy";

/**
 * Choose the popup transport for a normalized browser capture.
 *
 * Printables, MakerWorld, and Thingiverse are file-backed even when the adapter has no safe candidate. Keep
 * that source draft client-side and require a local file instead of allowing
 * it to fall through to the metadata-only inbox endpoint.
 */
export function browserCaptureRoute(capture: BrowserCaptureMessage | null): BrowserCaptureRoute {
  if (!capture) return "legacy";
  if (
    capture.source.provider === "printables" ||
    capture.source.provider === "makerworld" ||
    capture.source.provider === "thingiverse"
  ) {
    return capture.state === "ready" && capture.candidates.length > 0
      ? "candidate_confirmation"
      : "manual_file";
  }
  return capture.state === "manual_file_required" ? "manual_file" : "legacy";
}
