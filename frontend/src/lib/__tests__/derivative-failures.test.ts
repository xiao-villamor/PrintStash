/** New project readers distinguish malformed input from analysis resource limits. */
import { describe, expect, it } from "vitest";
import { derivativeFailureCause } from "../derivative-failures";

describe("project profile failure causes", () => {
  it("identifies malformed project data", () => {
    expect(derivativeFailureCause("project_profile_malformed")).toBe("file");
  });
  it("identifies bounded analysis limits", () => {
    expect(derivativeFailureCause("project_profile_limits")).toBe("too_large");
  });
});
