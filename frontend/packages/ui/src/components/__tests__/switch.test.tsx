/** Switch interactions expose the controlled state through accessible native controls. */
import { createRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Switch } from "../switch";

describe("Switch", () => {
  it("keeps the announced state synchronized with the visible state", async () => {
    const user = userEvent.setup();
    function Preference() {
      const [checked, setChecked] = useState(false);
      return <Switch label="Generate previews" checked={checked} onChange={setChecked} />;
    }
    render(<Preference />);
    const control = screen.getByRole("switch", { name: "Generate previews" });
    expect(control).not.toBeChecked();
    expect(screen.getByText("Off")).toBeVisible();
    await user.click(control);
    expect(control).toBeChecked();
    expect(screen.getByText("On")).toBeVisible();
    await user.keyboard(" ");
    expect(control).not.toBeChecked();
    expect(screen.getByText("Off")).toBeVisible();
  });

  it("does not change a disabled preference", async () => {
    const onChange = vi.fn<(checked: boolean) => void>();
    render(<Switch label="Generate previews" checked disabled onChange={onChange} />);
    await userEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("switch")).toBeChecked();
  });

  it("uses the caller's localized state words", () => {
    const props = {
      label: "Vistas previas",
      onChange: vi.fn<(checked: boolean) => void>(),
      stateLabels: { on: "Sí", off: "No" },
    };
    const { rerender } = render(<Switch {...props} checked />);
    expect(screen.getByText("Sí")).toBeVisible();
    rerender(<Switch {...props} checked={false} />);
    expect(screen.getByText("No")).toBeVisible();
  });

  it("keeps an accessible name when visible state words are suppressed", () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <Switch
        ref={ref}
        label="Generate previews"
        checked={false}
        stateLabels={null}
        onChange={vi.fn<(checked: boolean) => void>()}
      />,
    );
    expect(screen.queryByText("Off")).toBeNull();
    expect(ref.current).toBe(screen.getByRole("switch", { name: "Generate previews" }));
    ref.current?.focus();
    expect(screen.getByRole("switch")).toHaveFocus();
  });
});
