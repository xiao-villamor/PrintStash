import { describe, expect, it } from "vitest";
import { displayModelName } from "@/lib/display-name";

describe("displayModelName", () => {
  it("shows file-stem separators as single spaces", () => {
    expect(displayModelName("Shoe+Rack+-+Shelf+with+wall+mount")).toBe("Shoe Rack - Shelf with wall mount");
    expect(displayModelName("skadis_container__60-65-90")).toBe("skadis container 60-65-90");
  });
  it("keeps a name made only of separators rather than showing nothing", () => {
    expect(displayModelName("+_+")).toBe("+_+");
  });
});
