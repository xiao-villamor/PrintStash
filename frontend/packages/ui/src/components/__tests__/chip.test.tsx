/*
 * The quiet status pill shared by revision cards and multipart parts.
 *
 * Its contract is that the neutral tone stays uncoloured and each semantic tone
 * tints only lightly: a tone silently falling back would repaint every ordinary
 * status in the failure colour, or hide a real failure. The icon is decoration, so
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

  it.each([
    ["success", "bg-success/10", "text-success"],
    ["warning", "bg-warning/10", "text-warning"],
    ["destructive", "bg-destructive/10", "text-destructive"],
  ] as const)("tints the %s tone lightly", (tone, fill, text) => {
    render(<Chip tone={tone}>State</Chip>);

    const chip = screen.getByText("State");
    expect(chip).toHaveAttribute("data-tone", tone);
    expect(chip).toHaveClass(fill, text);
    expect(chip).not.toHaveClass("bg-foreground/10");
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
