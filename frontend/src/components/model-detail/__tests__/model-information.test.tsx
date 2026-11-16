/* Model facts belong to the source file. Missing geometry stays unknown, zero
 * stays zero, and untrusted legacy source URLs never become executable links. */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ModelInformation } from "../model-information";
import { aMetadata, aModel, aRevision } from "@/test-support/factories";
import type { FileRead } from "@/types";

function show(
  sourceUrl: string | null = null,
  sourceFile: FileRead | null = null,
  canEdit = false,
) {
  return render(
    <ModelInformation
      model={aModel({ source_url: sourceUrl })}
      sourceFile={sourceFile}
      canEdit={canEdit}
      onEdit={() => {}}
      onTags={() => {}}
    />,
  );
}

describe("ModelInformation", () => {
  it("normalizes safe source links", () => {
    show("HTTPS://EXAMPLE.TEST/cube");
    expect(screen.getByRole("link", { name: "Source model" })).toHaveAttribute(
      "href",
      "https://example.test/cube",
    );
  });
  it.each(["javascript:alert(1)", "https://user:secret@example.test/cube", "data:text/html,hello"])(
    "does not link unsafe source %s",
    (source) => {
      show(source);
      expect(screen.queryByRole("link", { name: "Source model" })).not.toBeInTheDocument();
    },
  );
  it("keeps unavailable geometry explicit", () => {
    show();
    expect(screen.getAllByText("Not recorded")).toHaveLength(3);
  });
  it("does not discard recorded zero geometry", () => {
    show(
      null,
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
    expect(screen.getByText("0mm × 12mm × 30mm")).toBeInTheDocument();
    expect(screen.getByText("0 cm³")).toBeInTheDocument();
    expect(screen.queryByText("Not recorded")).not.toBeInTheDocument();
  });
  it("only offers editing to permitted users", () => {
    const view = show();
    expect(screen.queryByRole("button", { name: "Edit details" })).not.toBeInTheDocument();
    view.unmount();
    show(null, null, true);
    expect(screen.getByRole("button", { name: "Edit details" })).toBeInTheDocument();
  });
});
