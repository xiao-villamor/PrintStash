import { describe, expect, it } from "vitest";
import { previousCaptures, captureHistoryLabel } from "../source-history.ts";
describe("previous publication captures", () => {
  it("queries only the source page without its tracking or profile fragment", async () => {
    let requested = "";
    const rows = await previousCaptures(
      "https://vault.test/",
      "test-device",
      "https://makerworld.com/es/models/1-part?from=test#profileId-2",
      async (url) => {
        requested = String(url);
        return Response.json([{ id: 1, state: "completed", completion: "partial" }]);
      },
    );
    expect(new URL(requested).searchParams.get("source_url")).toBe(
      "https://makerworld.com/es/models/1-part",
    );
    expect(captureHistoryLabel(rows[0])).toBe("Partially imported");
  });
  it("does not present an unavailable query as no duplicates", async () => {
    await expect(
      previousCaptures(
        "https://vault.test",
        "test-device",
        "https://makerworld.com/models/1-part",
        async () => new Response(null, { status: 403 }),
      ),
    ).rejects.toThrow("unavailable");
  });
  it("rejects malformed result identities", async () => {
    await expect(
      previousCaptures(
        "https://vault.test",
        "test-device",
        "https://makerworld.com/models/1-part",
        async () => Response.json([{ id: -1, state: "completed", completion: "complete" }]),
      ),
    ).rejects.toThrow("Invalid");
  });
});
