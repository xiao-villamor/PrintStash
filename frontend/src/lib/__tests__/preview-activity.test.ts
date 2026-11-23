/** Preview progress reports server counts, including incomplete and failed work. */
import { describe, expect, it } from "vitest";
import { aPreviewActivity } from "@/test-support/factories";
import { previewProgress } from "../preview-activity";

describe("previewProgress", () => {
  it("reports completed work without estimating from queue size", () => {
    expect(
      previewProgress(aPreviewActivity({ total: 100, completed: 25, remaining: 75 })),
    ).toMatchObject({
      total: 100,
      processed: 25,
      succeeded: 25,
      progress: 25,
      detail: "100 preview tasks · 25 done · 75 remaining · 0 skipped · 0 failed",
    });
  });
  it("reports non-generated tasks separately", () => {
    expect(
      previewProgress(
        aPreviewActivity({ total: 100, completed: 25, remaining: 50, skipped: 20, failed: 5 }),
      ),
    ).toMatchObject({
      processed: 50,
      succeeded: 25,
      skipped: 20,
      failed: 5,
      progress: 50,
    });
  });
  it("handles an empty library", () => {
    expect(previewProgress(aPreviewActivity()).progress).toBe(100);
  });
});
