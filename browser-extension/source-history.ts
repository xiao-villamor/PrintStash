import { captureUrlForVault } from "./core.ts";
import { readBoundedMetadataResponse } from "./capture-adapter.ts";

const states = [
  "captured",
  "resolving",
  "review",
  "importing",
  "completed",
  "failed",
  "dismissed",
] as const;
type CaptureState = (typeof states)[number];
export interface PreviousCapture {
  id: number;
  resulting_model_id?: number;
  state: CaptureState;
  completion: "complete" | "partial" | null;
}
export async function previousCaptures(
  vault: string,
  authorization: string,
  sourceUrl: string,
  fetchImpl = fetch,
): Promise<PreviousCapture[]> {
  const source = captureUrlForVault(sourceUrl);
  const response = await fetchImpl(
    `${vault.replace(/\/$/, "")}/api/v1/inbox/capture-source-status?${new URLSearchParams({ source_url: source, limit: "5" })}`,
    {
      headers: { Authorization: `Bearer ${authorization}` },
      signal: AbortSignal.timeout(10_000),
    },
  );
  if (!response.ok) throw new Error("Capture history is unavailable.");
  const value: unknown = JSON.parse(await readBoundedMetadataResponse(response, 32 * 1024));
  if (!Array.isArray(value) || value.length > 5) throw new Error("Invalid capture history.");
  return value.map((item: unknown) => {
    if (
      typeof item !== "object" ||
      item === null ||
      !("id" in item) ||
      typeof item.id !== "number" ||
      !Number.isSafeInteger(item.id) ||
      item.id < 1 ||
      !("state" in item) ||
      !states.includes(item.state as CaptureState) ||
      !("completion" in item) ||
      ![null, "complete", "partial"].includes(item.completion as null | string)
    )
      throw new Error("Invalid capture history.");
    return {
      id: item.id,
      ...("resulting_model_id" in item &&
      typeof item.resulting_model_id === "number" &&
      Number.isSafeInteger(item.resulting_model_id) &&
      item.resulting_model_id > 0
        ? { resulting_model_id: item.resulting_model_id }
        : {}),
      state: item.state as CaptureState,
      completion: item.completion as PreviousCapture["completion"],
    };
  });
}
export function captureHistoryLabel(item: PreviousCapture): string {
  if (item.completion === "partial") return "Partially imported";
  if (item.state === "completed") return "Imported";
  if (item.state === "failed") return "Needs attention";
  return "Pending";
}
