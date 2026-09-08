/** Storage operation reason codes become localized recovery copy at the UI boundary. */
import { describe, expect, it } from "vitest";

import { storageOperationMessage } from "@/lib/storage-operations";

describe("storageOperationMessage", () => {
  it("maps a known availability code", () => {
    expect(storageOperationMessage("storage_dependency_missing", (key) => key)).toBe(
      "storage.fullImageRequired",
    );
  });

  it("uses localized recovery copy for an unknown code", () => {
    expect(storageOperationMessage("future_storage_reason", (key) => key)).toBe(
      "storage.operationUnavailable",
    );
  });
});
