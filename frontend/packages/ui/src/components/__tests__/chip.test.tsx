/*
 * The quiet status pill shared by revision cards and multipart parts.
 *
 * Its contract is that the neutral tone stays neutral and only `destructive`
 * is loud: a tone silently falling back would repaint every ordinary status
 * in the failure colour, or hide a real failure. The icon is decoration, so
 * it must never be announced, and the caller's own attributes must survive.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Chip } from "../chip";

describe("Chip", () => {
  it("renders its label with the neutral tone by default", () => {
    render(<Chip>Ready</Chip>);

    const chip = screen.getByText("Ready");
    expect(chip).toHaveAttribute("data-tone", "neutral");
    expect(chip).toHaveClass("text-foreground/80");
  });

  it("takes the destructive tone for an exception", () => {
    render(<Chip tone="destructive">Failed</Chip>);

    const chip = screen.getByText("Failed");
    expect(chip).toHaveAttribute("data-tone", "destructive");
    expect(chip).toHaveClass("text-destructive");
    expect(chip).not.toHaveClass("text-foreground/80");
  });

  it("hides its icon from assistive technology", () => {
    render(<Chip icon={<svg data-testid="icon" />}>Ready</Chip>);

    expect(screen.getByTestId("icon").parentElement).toHaveAttribute("aria-hidden", "true");
    expect(screen.getByText("Ready")).toHaveClass("pl-1.5");
  });

  it("uses wider padding without an icon", () => {
    render(<Chip>Ready</Chip>);

    expect(screen.getByText("Ready")).toHaveClass("pl-2");
  });

  it("passes the caller's attributes through", () => {
    render(
      <Chip aria-label="Revision status: Ready" title="Ready" className="max-w-24">
        Ready
      </Chip>,
    );

    const chip = screen.getByLabelText("Revision status: Ready");
    expect(chip).toHaveAttribute("title", "Ready");
    expect(chip).toHaveClass("max-w-24");
  });
});
