/* Inspecting a revision is local selection. A stored recommendation remains
 * distinguishable, can be returned to, and recovers if the inspected file disappears. */
import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { RevisionPicker } from "../revision-picker";
import { inspectedRevision } from "../inspected-revision";
import { aRevision } from "@/test-support/factories";

const recommended = aRevision({ id: 1, version: 1, is_recommended: true });
const latest = aRevision({ id: 2, version: 2, original_filename: "latest.gcode" });

describe("RevisionPicker", () => {
  it("preserves the recommendation while inspecting another revision", async () => {
    const user = userEvent.setup();
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
    const trigger = () => screen.getByRole("button", { name: /^Rev \d/ });
    expect(trigger()).toHaveAccessibleName(/^Rev 1/);

    await user.click(trigger());
    expect(screen.getByRole("menuitemradio", { name: /^Rev 1.*Recommended/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(screen.getByRole("menuitemradio", { name: /^Rev 2/ }));
    expect(trigger()).toHaveAccessibleName(/^Rev 2/);
    expect(trigger()).toHaveAttribute("title", "latest.gcode");

    await user.click(trigger());
    expect(screen.getByRole("menuitemradio", { name: /^Rev 2/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    await user.click(screen.getByRole("menuitemradio", { name: /^Rev 1.*Recommended/ }));
    expect(trigger()).toHaveAccessibleName(/^Rev 1/);
    expect(files.map((file) => file.is_recommended)).toEqual([true, false]);
  });
});
