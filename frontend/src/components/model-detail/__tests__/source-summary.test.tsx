/** Captured source terms and local corrections stay distinct at decision points. */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { json, renderApp } from "@/test-support/render";
import { CaptureSourceSummary, ModelSourceSummary } from "../source-summary";

describe("SourceSummary", () => {
  it("offers the source section when no source was captured", () => {
    renderApp(<ModelSourceSummary modelId={1} />, {
      routes: {
        "GET /api/v1/models/1/provenance": json({
          edit_epoch: "a".repeat(32),
          edit_version: 1,
          sources: [],
        }),
      },
    });
    expect(screen.getByRole("link", { name: "View source details" })).toHaveAttribute(
      "href",
      "/models/1?tab=source",
    );
    expect(screen.queryByText(/Captured license/)).not.toBeInTheDocument();
  });

  it("normalizes safe source links", () => {
    renderApp(<ModelSourceSummary modelId={1} sourceUrl="HTTPS://EXAMPLE.TEST/cube" />);
    expect(screen.getByRole("link", { name: "Source model" })).toHaveAttribute(
      "href",
      "https://example.test/cube",
    );
  });
  it.each(["javascript:alert(1)", "https://user:secret@example.test/cube", "data:text/html,hello"])(
    "does not link unsafe source %s",
    (source) => {
      renderApp(<ModelSourceSummary modelId={1} sourceUrl={source} />);
      expect(screen.queryByRole("link", { name: "Source model" })).not.toBeInTheDocument();
    },
  );
  it("identifies inferred terms without inventing rights", () => {
    render(
      <CaptureSourceSummary
        manifest={{
          schema_version: 2,
          kind: "model_files",
          files: [],
          selected_ids: [],
          source: {
            provider: "printables",
            canonical_url: "https://www.printables.com/model/1",
            source_item_id: "1",
            source_revision: null,
            adapter_version: "test",
            tags: [],
            fields: { license_text: { value: "Custom terms", origin: "inferred" } },
          },
        }}
      />,
    );
    expect(screen.getByText(/Custom terms.*Inferred/)).toBeVisible();
    expect(screen.getByText("Not captured")).toBeVisible();
  });
  it("shows an override beside the original source terms", async () => {
    renderApp(<ModelSourceSummary modelId={1} />, {
      routes: {
        "GET /api/v1/models/1/provenance": json({
          edit_epoch: "a".repeat(32),
          edit_version: 1,
          sources: [
            {
              id: 1,
              provider: "printables",
              first_captured_at: "2026-11-16T10:00:00Z",
              fields: [
                {
                  field_name: "license_text",
                  captured_value: "Original terms",
                  captured_origin: "confirmed",
                  user_override_set: true,
                  user_value: "Local correction",
                  effective_value: "Local correction",
                  effective_origin: "user",
                },
              ],
            },
          ],
        }),
      },
    });
    expect(await screen.findByText("Original terms")).toBeVisible();
    expect(screen.getByText("Captured license", { exact: true })).toBeVisible();
    expect(screen.getByText("Local correction")).toBeVisible();
    expect(screen.getByText("Your correction", { exact: true })).toBeVisible();
    expect(screen.getByRole("link", { name: "View source details" })).toHaveAttribute(
      "href",
      "/models/1?tab=source",
    );
  });
});
