/* Model facts belong to the source file. Missing geometry stays unknown, zero
 * stays zero, and untrusted legacy source URLs never become executable links. */
import { renderApp, json } from "@/test-support/render";
import "@testing-library/jest-dom/vitest";
import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelGeometry, ModelInformation } from "../model-information";
import userEvent from "@testing-library/user-event";
import { aMetadata, aModel, aRevision, aTag } from "@/test-support/factories";
import type { FileRead } from "@/types";

function show(sourceUrl: string | null = null, canEdit = false, description: string | null = null) {
  return renderApp(
    <ModelInformation
      model={aModel({ source_url: sourceUrl, description })}
      canEdit={canEdit}
      tagSuggestions={[]}
      onTagsSaved={() => {}}
    />,
    {
      routes: {
        "GET /api/v1/models/1/provenance": json({
          edit_epoch: "a".repeat(32),
          edit_version: 1,
          sources: [],
        }),
        "GET /api/v1/models/1/related-profiles": json({ items: [], has_more: false }),
      },
    },
  );
}

function showGeometry(sourceFile: FileRead | null) {
  return renderApp(<ModelGeometry modelId={1} sourceFile={sourceFile} />, {
    routes: { "GET /api/v1/files/1/derivatives": json([]) },
  });
}

describe("ModelInformation", () => {
  it("renders a Markdown model description", () => {
    show(
      null,
      false,
      "## Assembly\n\n- **Base**\n- Top\n\n[Guide](https://example.test/guide)\n\n| Part | Count |\n| --- | --- |\n| Screw | 4 |",
    );
    expect(screen.getByRole("heading", { name: "Assembly" })).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((item) => item.textContent)).toEqual([
      "Base",
      "Top",
    ]);
    expect(screen.getByText("Base").tagName).toBe("STRONG");
    expect(screen.getByRole("link", { name: "Guide" })).toHaveAttribute(
      "href",
      "https://example.test/guide",
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.queryByText("## Assembly")).not.toBeInTheDocument();
  });
  it("rejects executable description content", () => {
    show(
      null,
      false,
      '<script>window.unsafe=true</script>\n\n<img src="x" onerror="window.unsafe=true">\n\n[unsafe](javascript:alert(1))\n\nSafe instructions',
    );
    expect(document.querySelector("script, [onerror], a[href^='javascript:']")).toBeNull();
    expect(screen.getByText("Safe instructions")).toBeInTheDocument();
  });
  it("preserves a plain description", () => {
    show(null, false, "Print upright with supports.");
    expect(screen.getByText("Print upright with supports.")).toBeInTheDocument();
  });
  it("only offers tag editing to permitted users", () => {
    const view = show();
    expect(screen.queryByRole("button", { name: /^Add tags to / })).not.toBeInTheDocument();
    view.unmount();
    show(null, true);
    expect(screen.getByRole("button", { name: /^Add tags to / })).toBeInTheDocument();
  });

  it("applies a checked tag immediately and displays the server receipt", async () => {
    const user = userEvent.setup();
    const model = aModel();
    const onTagsSaved =
      vi.fn<(tags: string[], base: { edit_epoch: string; edit_version: number }) => void>();
    const savedVersion = { edit_epoch: model.edit_epoch, edit_version: model.edit_version + 1 };
    renderApp(
      <ModelInformation
        model={model}
        canEdit
        tagSuggestions={[aTag()]}
        onTagsSaved={onTagsSaved}
      />,
      {
        routes: {
          "POST /api/v1/models/batch/tags": json({
            succeeded_count: 1,
            succeeded_ids: [model.id],
            succeeded_versions: { [model.id]: savedVersion },
            failed: [],
          }),
        },
      },
    );
    await user.click(screen.getByRole("button", { name: /^Add tags to / }));
    await user.click(screen.getByRole("option", { name: /functional.*3/ }));
    expect(await screen.findByText("Saved", { exact: true })).toBeVisible();
    expect(onTagsSaved).toHaveBeenCalledExactlyOnceWith(["functional"], savedVersion);
    expect(screen.getByRole("option", { name: /functional.*3/ })).toHaveAttribute(
      "aria-selected",
      "true",
    );
  });

  it("places the compact tag editor beside the assigned tags", () => {
    renderApp(
      <ModelInformation
        model={aModel({ tags: ["PLA"] })}
        canEdit
        tagSuggestions={[]}
        onTagsSaved={() => {}}
      />,
    );
    expect(screen.getByText("PLA")).toBeVisible();
    expect(screen.getByRole("button", { name: /^Edit tags for / })).toHaveTextContent(/^$/);
  });
});

describe("ModelGeometry", () => {
  it("labels the mesh geometry as the whole source", () => {
    showGeometry(aRevision({ file_type: "stl" }));
    expect(screen.getByRole("heading", { name: "Whole source geometry" })).toBeInTheDocument();
  });
  it("explains unavailable mesh measurements", () => {
    showGeometry(aRevision({ file_type: "stl" }));
    expect(screen.getByText("Measurement unavailable")).toBeVisible();
  });
  it("hides geometry when there is no mesh", () => {
    showGeometry(null);
    expect(screen.queryByRole("heading", { name: "Whole source geometry" })).toBeNull();
  });
  it("does not discard recorded zero geometry", () => {
    showGeometry(
      aRevision({
        file_type: "stl",
        metadata: aMetadata({
          bbox_x_mm: 0,
          bbox_y_mm: 12,
          bbox_z_mm: 30,
          volume_mm3: 0,
          triangle_count: 0,
        }),
      }),
    );
    expect(screen.getByText("0 × 12 × 30 mm")).toBeInTheDocument();
    expect(screen.getByText("0 cm³")).toBeInTheDocument();
    expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  });
});
