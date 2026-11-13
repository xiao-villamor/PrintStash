/* Inspecting a revision is local selection. A stored recommendation remains
 * distinguishable, can be returned to, and recovers if the inspected file disappears. */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { RevisionPicker } from "../revision-picker";
import { inspectedRevision } from "../inspected-revision";
import { aRevision } from "@/test-support/factories";

const recommended = aRevision({ id: 1, version: 1, is_recommended: true });
const latest = aRevision({ id: 2, version: 2, original_filename: "latest.gcode" });

describe("RevisionPicker", () => {
  it("preserves the recommendation while inspecting another revision", () => {
    const files = [recommended, latest];
    function Picker() {
      const [selectedId, select] = useState<number | null>(null);
      return (
        <RevisionPicker
          files={files}
          selected={inspectedRevision(files, selectedId)}
          onSelect={select}
        />
      );
    }
    render(<Picker />);
    expect(screen.getByRole("button", { name: /Rev 1/ })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Rev 2" }));
    expect(screen.getByRole("button", { name: "Rev 2" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("latest.gcode")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Return to recommended revision" }));
    expect(screen.getByRole("button", { name: /Rev 1/ })).toHaveAttribute("aria-pressed", "true");
    expect(files.map((file) => file.is_recommended)).toEqual([true, false]);
  });
});
