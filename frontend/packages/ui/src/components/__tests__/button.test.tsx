/*
 * The button primitive every action in the product is built from.
 *
 * Three things here are contracts rather than styling. `asChild` swaps the rendered
 * element for the caller's own — that is how a link gets button styling — and a
 * regression turns every styled link back into a nested `<button><a>`, which is
 * invalid HTML and unreachable by keyboard. `loading` must disable the button, not
 * merely draw a spinner: a submit that stays clickable while its request is in
 * flight is a double-submit. And a caller's `className` has to beat the variant's
 * own padding, which is the whole reason these components route through `cn`.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { Button, buttonVariants } from "../button";

describe("Button", () => {
  it("renders a button carrying the default variant and size", () => {
    render(<Button>Save</Button>);

    expect(screen.getByRole("button", { name: "Save" })).toHaveClass("bg-primary", "h-10");
  });

  it("swaps in the classes of the requested variant and size", () => {
    render(
      <Button variant="destructive" size="sm">
        Delete
      </Button>,
    );

    expect(screen.getByRole("button", { name: "Delete" })).toHaveClass("bg-destructive", "h-9");
  });

  it("lets a caller's class override the variant's own", () => {
    render(<Button className="px-8">Save</Button>);

    const button = screen.getByRole("button", { name: "Save" });
    expect(button).toHaveClass("px-8");
    expect(button).not.toHaveClass("px-4");
  });

  it("calls its click handler", () => {
    const onClick = vi.fn<() => void>();
    render(<Button onClick={onClick}>Save</Button>);

    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("forwards its ref to the underlying button", () => {
    const ref = createRef<HTMLButtonElement>();

    render(<Button ref={ref}>Save</Button>);

    expect(ref.current).toBe(screen.getByRole("button", { name: "Save" }));
  });

  it("renders the caller's own element under asChild", () => {
    render(
      <Button asChild>
        <a href="/models">Browse</a>
      </Button>,
    );

    const link = screen.getByRole("link", { name: "Browse" });
    expect(link).toHaveClass("bg-primary");
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("disables itself while loading", () => {
    render(<Button loading>Save</Button>);

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("shows a spinner while loading", () => {
    const { container } = render(<Button loading>Save</Button>);

    expect(container.querySelector(".animate-spin")).toBeInTheDocument();
  });

  it("stays disabled when the caller disables it", () => {
    render(<Button disabled>Save</Button>);

    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
  });

  it("exposes its variants for callers that style their own element", () => {
    expect(buttonVariants({ variant: "ghost", size: "icon" })).toContain("h-10 w-10");
  });
});
