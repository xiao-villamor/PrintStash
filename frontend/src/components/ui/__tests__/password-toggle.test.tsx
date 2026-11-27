/*
 * Every password field reveals its value with the same control, which states
 * whether it is pressed and which field it controls.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { PasswordToggle } from "@/components/ui/password-toggle";

describe("PasswordToggle", () => {
  it("reports its pressed state plus the field it controls", () => {
    const toggle = vi.fn<() => void>();
    render(
      <div className="relative">
        <PasswordToggle visible={false} onToggle={toggle} label="Show password" controls="pw" />
      </div>,
    );
    const button = screen.getByRole("button", { name: "Show password" });
    expect(button).toHaveAttribute("aria-pressed", "false");
    expect(button).toHaveAttribute("aria-controls", "pw");
    fireEvent.click(button);
    expect(toggle).toHaveBeenCalledTimes(1);
  });
});
