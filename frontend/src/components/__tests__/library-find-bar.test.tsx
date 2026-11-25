/*
 * The find bar walks matches one at a time. Everything the person sees comes
 * from these rules: matching ignores case, Enter wraps around, Shift+Enter goes
 * back, and the bar reports each match to the grid so it can scroll there.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LibraryFindBar } from "@/components/library-find-bar";

const MODELS = [
  { id: 1, name: "Alpha bracket" },
  { id: 2, name: "Beta mount" },
  { id: 3, name: "Gamma Bracket" },
];

function renderBar() {
  const onReveal = vi.fn<(id: number | null) => void>();
  const onClose = vi.fn<() => void>();
  render(<LibraryFindBar models={MODELS} onReveal={onReveal} onClose={onClose} />);
  return { onReveal, onClose, field: screen.getByRole("textbox", { name: "Find in collection" }) };
}

describe("LibraryFindBar", () => {
  it("takes focus when it opens", () => {
    const { field } = renderBar();

    expect(field).toHaveFocus();
  });

  it("counts matches regardless of case and reveals the first", async () => {
    const user = userEvent.setup();
    const { field, onReveal } = renderBar();

    await user.type(field, "BRACKET");

    expect(screen.getByRole("status")).toHaveTextContent("1 of 2");
    expect(onReveal).toHaveBeenLastCalledWith(1);
  });

  it("steps forward with Enter, wraps, and steps back with Shift+Enter", async () => {
    const user = userEvent.setup();
    const { field, onReveal } = renderBar();
    await user.type(field, "bracket");

    await user.keyboard("{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2");
    expect(onReveal).toHaveBeenLastCalledWith(3);

    await user.keyboard("{Enter}");
    expect(screen.getByRole("status")).toHaveTextContent("1 of 2");

    await user.keyboard("{Shift>}{Enter}{/Shift}");
    expect(screen.getByRole("status")).toHaveTextContent("2 of 2");
  });

  it("says so when nothing matches and disables stepping", async () => {
    const user = userEvent.setup();
    const { field, onReveal } = renderBar();

    await user.type(field, "zzz");

    expect(screen.getByRole("status")).toHaveTextContent("No results");
    expect(screen.getByRole("button", { name: "Next match" })).toBeDisabled();
    expect(onReveal).toHaveBeenLastCalledWith(null);
  });

  it("closes on Escape and on the close button", async () => {
    const user = userEvent.setup();
    const { field, onClose } = renderBar();

    await user.type(field, "{Escape}");
    await user.click(screen.getByRole("button", { name: "Close find" }));

    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("stops highlighting when it unmounts", () => {
    const onReveal = vi.fn<(id: number | null) => void>();
    const { unmount } = render(
      <LibraryFindBar models={MODELS} onReveal={onReveal} onClose={() => {}} />,
    );

    unmount();

    expect(onReveal).toHaveBeenLastCalledWith(null);
  });
});
