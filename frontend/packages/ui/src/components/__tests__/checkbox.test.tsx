/*
 * The selection control on every model card and bulk-action list.
 *
 * It is a `<button role="checkbox">` rather than an `<input>`, so the two things a
 * real checkbox gets for free have to be asserted here: `aria-checked` reporting the
 * current state, and a change reporting the *new* value rather than the old one.
 *
 * The click handler also stops propagation, and that is not cosmetic — these sit
 * inside cards that navigate on click. Without it, selecting a model opens it.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import { Checkbox } from "../checkbox";

describe("Checkbox", () => {
  it("reports an unchecked box to assistive technology", () => {
    render(
      <Checkbox
        checked={false}
        onChange={vi.fn<(checked: boolean) => void>()}
        ariaLabel="Select Benchy"
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Select Benchy" })).not.toBeChecked();
  });

  it("reports a checked box to assistive technology", () => {
    render(
      <Checkbox
        checked={true}
        onChange={vi.fn<(checked: boolean) => void>()}
        ariaLabel="Select Benchy"
      />,
    );

    expect(screen.getByRole("checkbox", { name: "Select Benchy" })).toBeChecked();
  });

  it("asks to be checked when it is not", () => {
    const onChange = vi.fn<(checked: boolean) => void>();
    render(<Checkbox checked={false} onChange={onChange} ariaLabel="Select Benchy" />);

    fireEvent.click(screen.getByRole("checkbox"));

    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("asks to be unchecked when it is", () => {
    const onChange = vi.fn<(checked: boolean) => void>();
    render(<Checkbox checked={true} onChange={onChange} ariaLabel="Select Benchy" />);

    fireEvent.click(screen.getByRole("checkbox"));

    expect(onChange).toHaveBeenCalledWith(false);
  });

  it("keeps its click from reaching the card behind it", () => {
    const onCardClick = vi.fn<() => void>();
    render(
      <div onClick={onCardClick}>
        <Checkbox
          checked={false}
          onChange={vi.fn<(checked: boolean) => void>()}
          ariaLabel="Select Benchy"
        />
      </div>,
    );

    fireEvent.click(screen.getByRole("checkbox"));

    expect(onCardClick).not.toHaveBeenCalled();
  });

  it("refuses to change while disabled", () => {
    const onChange = vi.fn<(checked: boolean) => void>();
    render(<Checkbox checked={false} onChange={onChange} disabled ariaLabel="Select Benchy" />);

    fireEvent.click(screen.getByRole("checkbox"));

    expect(onChange).not.toHaveBeenCalled();
  });

  it("lets a caller's class override its own", () => {
    render(
      <Checkbox
        checked={false}
        onChange={vi.fn<(checked: boolean) => void>()}
        className="h-8"
        ariaLabel="Select"
      />,
    );

    const box = screen.getByRole("checkbox");
    expect(box).toHaveClass("h-8");
    expect(box).not.toHaveClass("h-5");
  });

  it("forwards its ref to the underlying button", () => {
    const ref = createRef<HTMLButtonElement>();

    render(
      <Checkbox
        ref={ref}
        checked={false}
        onChange={vi.fn<(checked: boolean) => void>()}
        ariaLabel="Select"
      />,
    );

    expect(ref.current).toBe(screen.getByRole("checkbox"));
  });
});
