import { describe, expect, it } from "vitest";
import { newFamilyChoices } from "../family-choices";
import { aModelListItem } from "@/test-support/factories";
import { aFamilyMember } from "@/test-support/families";

describe("Family Choices", () => {
  it("preserves explicit selection order and excludes duplicates across Parts", () => {
    const existing = aFamilyMember();
    const second = aFamilyMember({
      id: 12,
      model_id: 2,
      model: aModelListItem({ id: 2, name: "Spatula" }),
    });
    const third = aFamilyMember({
      id: 13,
      model_id: 3,
      model: aModelListItem({ id: 3, name: "Long handle" }),
    });
    expect(newFamilyChoices([third, existing, second, third], new Set([1]))).toEqual([
      expect.objectContaining({ id: 3, name: "Long handle", available: true }),
      expect.objectContaining({ id: 2, name: "Spatula", available: true }),
    ]);
    expect(existing.role).toBe("canonical");
  });
});
