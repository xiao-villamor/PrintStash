/** Generic comparison keeps source and print context available without a preview. */
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyComparison } from "../comparison";
import { aModelListItem } from "@/test-support/factories";
import { aFamilyMember } from "@/test-support/families";
import { renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());
describe("Family comparison context", () => {
  it("localizes print outcomes in Spanish", () => {
    renderApp(
      <FamilyComparison
        members={[
          aFamilyMember({ latest_print_outcome: "completed" }),
          aFamilyMember({ id: 12, latest_print_outcome: "failed" }),
        ]}
      />,
      { locale: "es" },
    );
    expect(screen.getByRole("row", { name: /Última impresión/ })).toHaveTextContent("Completado");
    expect(screen.getByRole("row", { name: /Última impresión/ })).toHaveTextContent("Fallido");
  });
  it("shows each Model's separate source", () => {
    renderApp(
      <FamilyComparison
        members={[
          aFamilyMember({
            model: aModelListItem({ source_url: "https://printables.com/model/1" }),
          }),
          aFamilyMember({
            id: 12,
            model: aModelListItem({ id: 2, source_url: "https://makerworld.com/models/2" }),
          }),
        ]}
      />,
    );
    expect(screen.getByRole("row", { name: /Source https/ })).toHaveTextContent(
      "https://printables.com/model/1",
    );
    expect(screen.getByRole("row", { name: /Source https/ })).toHaveTextContent(
      "https://makerworld.com/models/2",
    );
  });
});
