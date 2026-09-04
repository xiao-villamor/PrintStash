/*
 * The hairline rule between sections and between toolbar groups.
 *
 * It is decorative by default, and that default is the point: a purely visual rule
 * announced as a separator adds noise to every screen reader pass over a toolbar. A
 * caller that means the division semantically opts in, and then the orientation has
 * to be announced too — a vertical rule reported as horizontal describes the wrong
 * layout.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Separator } from "../separator";

describe("Separator", () => {
  it("stays out of the accessibility tree by default", () => {
    render(<Separator data-testid="rule" />);

    expect(screen.getByTestId("rule")).toHaveAttribute("role", "none");
    expect(screen.queryByRole("separator")).toBeNull();
  });

  it("draws a full-width rule by default", () => {
    render(<Separator data-testid="rule" />);

    expect(screen.getByTestId("rule")).toHaveClass("h-[1px]", "w-full");
  });

  it("draws a full-height rule when vertical", () => {
    render(<Separator orientation="vertical" data-testid="rule" />);

    expect(screen.getByTestId("rule")).toHaveClass("h-full", "w-[1px]");
  });

  it("announces itself when the caller means it semantically", () => {
    render(<Separator decorative={false} orientation="vertical" />);

    expect(screen.getByRole("separator")).toHaveAttribute("aria-orientation", "vertical");
  });
});
