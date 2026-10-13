/** Only complete, session-owned gesture snapshots authorize a model drop. */
import { describe, expect, it } from "vitest";
import { captureModelDrag, readModelDrag } from "../model-dnd";
import { clearLogin } from "../auth-store";
import { aOutlinerModel } from "@/test-support/factories";

describe("model drag", () => {
  it("freezes the displayed version at gesture start", () => {
    const model = aOutlinerModel({ edit_version: 7 });
    const drag = captureModelDrag(model);
    model.edit_version = 8;
    expect(readModelDrag(JSON.stringify(drag))).toEqual({ ...drag, edit_version: 7 });
  });
  it.each([
    "1",
    "{",
    "null",
    "[]",
    JSON.stringify({ id: 1 }),
    ...[0, -1, 1.5, "7"].map((edit_version) =>
      JSON.stringify({ ...captureModelDrag(aOutlinerModel()), edit_version }),
    ),
  ])("rejects incomplete or malformed payload %s", (raw) => {
    expect(readModelDrag(raw)).toBeNull();
  });
  it("rejects a gesture from a retired session", () => {
    const raw = JSON.stringify(captureModelDrag(aOutlinerModel()));
    clearLogin();
    expect(readModelDrag(raw)).toBeNull();
  });
});
