/** A successful transfer is a reviewable capture, not an imported Model. */
export function captureReviewUrl(vault: string, item: unknown): string {
  if (
    typeof item !== "object" ||
    item === null ||
    !("id" in item) ||
    typeof item.id !== "number" ||
    !Number.isSafeInteger(item.id) ||
    item.id <= 0
  ) {
    throw new Error("PrintStash returned an invalid capture receipt.");
  }
  return `${vault.replace(/\/$/, "")}/inbox/${item.id}`;
}
