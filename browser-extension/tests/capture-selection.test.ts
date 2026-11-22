import { describe, expect, it } from "vitest";
import type { BrowserCaptureMessage } from "../capture-adapter.ts";
import { defaultCaptureSelection } from "../capture-selection.ts";

type Candidate = BrowserCaptureMessage["candidates"][number];

const packageFile: Candidate = { id: "58047", filename: "planter.3mf", fileType: "other" };
const otherPackage: Candidate = { id: "90123", filename: "planter-small.3mf", fileType: "other" };
const model: Candidate = { id: "model-1", filename: "cube.stl", fileType: "stl" };
const secondModel: Candidate = { id: "model-2", filename: "stand.obj", fileType: "other" };
const print: Candidate = {
  id: "printables:123:file:391",
  filename: "cube.gcode",
  fileType: "gcode",
};
const guide: Candidate = { id: "guide-1", filename: "guide.pdf", fileType: "other" };

function draft(
  provider: BrowserCaptureMessage["source"]["provider"],
  candidates: Candidate[],
): BrowserCaptureMessage {
  return {
    schema_version: 2,
    kind: "browser_source",
    state: "ready",
    source: {
      provider,
      canonical_url: "https://example.com/model",
      source_item_id: "123",
      source_revision: null,
      adapter_version: "fixture",
      tags: [],
      fields: {},
    },
    candidates,
  };
}

describe("default import selection", () => {
  it("selects a single MakerWorld package", () => {
    expect(
      defaultCaptureSelection(
        draft("makerworld", [packageFile]),
        "https://makerworld.com/en/models/52226",
      ),
    ).toEqual({ kind: "selected", files: [packageFile] });
  });

  it.each([
    ["https://makerworld.com/en/models/52226#profileId-90123", otherPackage],
    ["https://makerworld.com/en/models/52226?profileId=58047", packageFile],
  ])("selects the MakerWorld profile named by %s", (page, expected) => {
    expect(defaultCaptureSelection(draft("makerworld", [packageFile, otherPackage]), page)).toEqual(
      { kind: "selected", files: [expected] },
    );
  });

  it("selects the Printables print file explicitly named in the URL", () => {
    expect(
      defaultCaptureSelection(
        draft("printables", [model, print]),
        "https://www.printables.com/model/123#file-391",
      ),
    ).toEqual({ kind: "selected", files: [print] });
  });

  it("defaults to the model files on a mixed publication", () => {
    expect(
      defaultCaptureSelection(
        draft("thingiverse", [guide, model, secondModel, print]),
        "https://www.thingiverse.com/thing:123",
      ),
    ).toEqual({ kind: "selected", files: [model, secondModel] });
  });

  it("explains why otherwise indistinguishable packages need a choice", () => {
    expect(
      defaultCaptureSelection(
        draft("makerworld", [packageFile, otherPackage]),
        "https://makerworld.com/en/models/52226",
      ),
    ).toEqual({
      kind: "ambiguous",
      message: "Choose a package. This page lists several print options.",
    });
  });

  it("keeps an unavailable named file from silently selecting a different file", () => {
    expect(
      defaultCaptureSelection(
        draft("makerworld", [packageFile, otherPackage]),
        "https://makerworld.com/en/models/52226#profileId-404",
      ),
    ).toMatchObject({ kind: "ambiguous" });
  });

  it("returns the empty state for a publication without candidates", () => {
    expect(
      defaultCaptureSelection(draft("printables", []), "https://www.printables.com/model/123"),
    ).toEqual({ kind: "empty" });
  });
});
