/*
 * The text field behind every form in the product.
 *
 * It is a thin wrapper, so its contract is that it stays thin: the caller's `type`,
 * value, and handlers reach the real `<input>`, the ref points at that input (forms
 * focus the first invalid field imperatively), and `aria-invalid` styling comes from
 * the shared class string rather than from a prop this component would have to know
 * about.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { inputClasses } from "../../lib/input-classes";
import { Input } from "../input";

describe("Input", () => {
  it("renders a text field carrying the shared input classes", () => {
    render(<Input aria-label="Model name" />);

    expect(screen.getByLabelText("Model name")).toHaveClass(...inputClasses.split(" "));
  });

  it("honours the requested input type", () => {
    render(<Input type="password" aria-label="Access code" />);

    expect(screen.getByLabelText("Access code")).toHaveAttribute("type", "password");
  });

  it("reports what the user typed", () => {
    const onChange = vi.fn<() => void>();
    render(<Input aria-label="Model name" onChange={onChange} />);

    fireEvent.change(screen.getByLabelText("Model name"), { target: { value: "Benchy" } });

    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("lets a caller's class override its own", () => {
    render(<Input aria-label="Model name" className="h-8" />);

    const input = screen.getByLabelText("Model name");
    expect(input).toHaveClass("h-8");
    expect(input).not.toHaveClass("h-10");
  });

  it("forwards its ref to the underlying input", () => {
    const ref = createRef<HTMLInputElement>();

    render(<Input ref={ref} aria-label="Model name" />);

    expect(ref.current).toBe(screen.getByLabelText("Model name"));
  });
});
