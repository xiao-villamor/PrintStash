import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { LibraryCardSelection } from "@/components/library-card-selection";

function renderSelection(checked: boolean) {
  render(
    <LibraryCardSelection selecting checked={checked} label="Select Demo" onChange={vi.fn()} />,
  );
  return screen.getByRole("checkbox", { name: "Select Demo" });
}

describe("LibraryCardSelection", () => {
  it("draws an unchecked box as an outlined chip that reads over any image", () => {
    const box = renderSelection(false);

    expect(box).toHaveClass("border-input", "bg-card", "shadow-md");
  });

  it("keeps the checked fill of the shared checkbox", () => {
    const box = renderSelection(true);

    expect(box).toHaveClass("bg-accent");
    expect(box).not.toHaveClass("bg-card");
  });
});
