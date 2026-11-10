/*
 * Which cards of a Library view are new since it was last read. They ease in
 * once; everything else keeps its place. Ids of different kinds must not be
 * mistaken for one another, or a new folder would hide behind a Model with the
 * same number.
 */
import { describe, expect, it } from "vitest";

import { arrivingKeys, entryKeys } from "../library-arrivals";

describe("entryKeys", () => {
  it("counts repeated entries as one arrival identity", () => {
    expect(
      entryKeys(
        [{ id: 3 }, { id: 3 }],
        [
          { kind: "model", value: { id: 3 } },
          { kind: "model", value: { id: 3 } },
          { kind: "multipart", value: { id: 3 } },
        ],
      ),
    ).toEqual(["c:3", "m:3", "p:3"]);
  });
  it("names folders, Models, multipart sets apart", () => {
    expect(
      entryKeys(
        [{ id: 3 }],
        [
          { kind: "model", value: { id: 3 } },
          { kind: "multipart", value: { id: 3 } },
        ],
      ),
    ).toEqual(["c:3", "m:3", "p:3"]);
  });
});

describe("arrivingKeys", () => {
  it("is empty when nothing was added", () => {
    expect(arrivingKeys(new Set(["m:1", "m:2"]), ["m:2", "m:1"]).size).toBe(0);
  });

  it("is what the previous reading did not have", () => {
    expect([...arrivingKeys(new Set(["m:1"]), ["m:9", "m:1", "c:4"])]).toEqual(["m:9", "c:4"]);
  });

  it("ignores what disappeared", () => {
    expect(arrivingKeys(new Set(["m:1", "m:2"]), ["m:1"]).size).toBe(0);
  });
});
