/*
 * The loading placeholder every list and card shows before its data arrives.
 *
 * The animation is what distinguishes "still loading" from "loaded and empty", so
 * losing it turns every slow page into what looks like a rendering bug. Sizing always
 * comes from the caller, which means the caller's class has to survive the merge.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Skeleton } from "../skeleton";

describe("Skeleton", () => {
  it("animates while it stands in for content", () => {
    render(<Skeleton data-testid="placeholder" />);

    expect(screen.getByTestId("placeholder")).toHaveClass("animate-pulse");
  });

  it("takes its size from the caller", () => {
    render(<Skeleton className="h-24 w-full" data-testid="placeholder" />);

    expect(screen.getByTestId("placeholder")).toHaveClass("h-24", "w-full");
  });

  it("lets a caller's class override its own", () => {
    render(<Skeleton className="rounded-full" data-testid="placeholder" />);

    const placeholder = screen.getByTestId("placeholder");
    expect(placeholder).toHaveClass("rounded-full");
    expect(placeholder).not.toHaveClass("rounded-md");
  });
});
