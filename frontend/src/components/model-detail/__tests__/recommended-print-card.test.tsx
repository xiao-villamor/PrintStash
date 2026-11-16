/** The recommendation card describes Revisions without denying toolpaths in source archives. */
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { renderApp } from "@/test-support/render";
import { RecommendedPrintCard } from "../recommended-print-card";

describe("RecommendedPrintCard", () => {
  it("explains that a separate G-code revision is missing", () => {
    renderApp(
      <RecommendedPrintCard
        file={null}
        hasGcode={false}
        saving={null}
        onSend={() => {}}
        canSend={false}
        onCompare={() => {}}
        onMark={() => {}}
        onAddRevision={() => {}}
      />,
    );

    expect(
      screen.getByText(
        "No G-code revision yet. Add a revision to capture the settings that worked.",
      ),
    ).toBeVisible();
  });
});
