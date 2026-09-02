import { describe, expect, it } from "vitest";

import { buildBrowserCaptureMessage, type BrowserCaptureMessage } from "../capture-adapter.ts";
import { browserCaptureRoute } from "../capture-routing.ts";

describe("browser capture popup routing", () => {
  it("fails closed for a Printables source with inconsistent ready and zero-candidate metadata", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
      pageTitle: "3DBenchy",
    });
    const inconsistentCapture: BrowserCaptureMessage = {
      ...capture,
      state: "ready",
      candidates: [],
    };

    expect(browserCaptureRoute(inconsistentCapture)).toBe("manual_file");
  });

  it("uses the normalized source provider when deciding whether candidates are safe", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Printables",
      pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
      pageTitle: "3DBenchy",
    });
    const withCandidates: BrowserCaptureMessage = {
      ...capture,
      state: "ready",
      candidates: [{ id: "file-1", filename: "benchy.3mf", fileType: "other" }],
    };

    expect(browserCaptureRoute(withCandidates)).toBe("candidate_confirmation");
  });

  it("routes a ready Thingiverse archive through candidate confirmation", () => {
    const capture = buildBrowserCaptureMessage({
      provider: "Thingiverse",
      pageUrl: "https://www.thingiverse.com/thing:7401604/files",
      pageTitle: "Cable Mount",
    });
    const withArchive: BrowserCaptureMessage = {
      ...capture,
      state: "ready",
      candidates: [
        {
          id: "thingiverse:7401604:archive",
          filename: "thingiverse-7401604.zip",
          fileType: "other",
        },
      ],
    };

    expect(browserCaptureRoute(withArchive)).toBe("candidate_confirmation");
  });
});
