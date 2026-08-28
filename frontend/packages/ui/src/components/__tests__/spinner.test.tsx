/*
 * The busy indicator inside buttons, panels, and the import queue.
 *
 * A spinner is pure motion, which means a sighted user learns "something is
 * happening" and a screen-reader user learns nothing at all — unless it carries a
 * status role and a label. Both are asserted here, along with the label being
 * injectable, because this package is locale-neutral and the consuming app owns
 * every user-visible string.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Spinner } from "../spinner";

describe("Spinner", () => {
  it("announces itself as a status with a default label", () => {
    render(<Spinner />);

    expect(screen.getByRole("status", { name: "Loading" })).toBeInTheDocument();
  });

  it("uses the label the application injected", () => {
    render(<Spinner label="Importing" />);

    expect(screen.getByRole("status", { name: "Importing" })).toBeInTheDocument();
  });

  it("renders at the medium size by default", () => {
    render(<Spinner />);

    expect(screen.getByRole("status")).toHaveClass("h-5", "w-5");
  });

  it("renders at the requested size", () => {
    render(<Spinner size="lg" />);

    expect(screen.getByRole("status")).toHaveClass("h-6", "w-6");
  });

  it("lets a caller's class override the size", () => {
    render(<Spinner size="sm" className="h-8 w-8" />);

    const spinner = screen.getByRole("status");
    expect(spinner).toHaveClass("h-8", "w-8");
    expect(spinner).not.toHaveClass("h-3.5");
  });
});
