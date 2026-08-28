/*
 * Focus stays inside an open overlay, even when its parent re-renders.
 *
 * A focus trap that is rebuilt on re-render drops focus back to the document,
 * and a modal you can tab out of is a modal a keyboard user cannot use — they
 * end up interacting with the page behind it while it still looks open. Parent
 * re-renders are constant in this app (a query settles, a poll returns), so
 * "across re-renders" is the real condition rather than an edge case.
 */

import "@testing-library/jest-dom/vitest";
import { describe, expect, it } from "vitest";
import { useState } from "react";
import { act, render, screen } from "@testing-library/react";

import { Modal } from "@/components/ui/modal";

/**
 * A parent re-render (the toolbar pill's entrance transition does one, two
 * frames after mount) must not pull focus out of whatever the user is typing
 * into inside an open dialog.
 */
function Harness() {
  const [, setTick] = useState(0);
  return (
    <>
      <button type="button" onClick={() => setTick((t) => t + 1)}>
        rerender
      </button>
      {/* new onClose identity on every parent render */}
      <Modal open onClose={() => {}} title="Tag">
        <input placeholder="tags" />
      </Modal>
    </>
  );
}

describe("useOverlayBehavior", () => {
  it("keeps focus inside the panel across parent re-renders", () => {
    render(<Harness />);
    const input = screen.getByPlaceholderText("tags");
    act(() => input.focus());

    act(() => screen.getByRole("button", { name: "rerender" }).click());

    expect(input).toHaveFocus();
  });
});
