import { describe, expect, it } from "vitest";

import { buildStatusPresentation } from "../status-presentation.ts";

describe("capture status presentation", () => {
  it("hides internal capture prefixes from the primary message", () => {
    const presentation = buildStatusPresentation({
      message: "user_file_required: Attach the downloaded package below.",
      kind: "error",
      diagnosticCode: "makerworld_links_failed",
      providerCode: "challenge",
    });

    expect(presentation.message).toBe("Attach the downloaded package below.");
    expect(presentation.message).not.toContain("user_file_required");
    expect(presentation.technicalCode).toBe("makerworld_links_failed · challenge");
  });

  it("keeps successful imports free of technical diagnostics", () => {
    expect(
      buildStatusPresentation({
        message: "MakerWorld packages sent to Pending Imports.",
        kind: "success",
      }),
    ).toEqual({
      title: "Sent to Pending Imports",
      message: "MakerWorld packages sent to Pending Imports.",
      kind: "success",
    });
  });
});
