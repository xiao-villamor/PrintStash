import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Badge } from "@/components/ui/badge";
import { SettingsCard, SettingsSwitch } from "@/components/settings-card";

describe("SettingsSwitch", () => {
  it("states its value as text and reports the next value", async () => {
    const onChange = vi.fn<(checked: boolean) => void>();
    render(<SettingsSwitch checked={false} onChange={onChange} label="Enable SSO login" />);

    const toggle = screen.getByRole("switch", { name: "Enable SSO login" });
    expect(toggle).toHaveAttribute("aria-checked", "false");
    expect(screen.getByText("Off")).toBeVisible();

    await userEvent.click(toggle);

    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("does not report a change while disabled", async () => {
    const onChange = vi.fn<(checked: boolean) => void>();
    render(<SettingsSwitch checked onChange={onChange} label="Allow HTTP" disabled />);

    expect(screen.getByText("On")).toBeVisible();
    await userEvent.click(screen.getByRole("switch", { name: "Allow HTTP" }));

    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("SettingsCard", () => {
  it("names its group by its title and keeps the action beside the heading", () => {
    render(
      <SettingsCard
        title="Version"
        description="Installed release"
        action={<button>GitHub</button>}
      >
        <p>body</p>
      </SettingsCard>,
    );

    const group = screen.getByRole("group", { name: "Version" });
    expect(group).toContainElement(screen.getByRole("heading", { name: "Version" }));
    expect(group).toContainElement(screen.getByRole("button", { name: "GitHub" }));
    expect(screen.getByText("Installed release")).toBeVisible();
  });
});

describe("Badge", () => {
  it("exposes its slot and variant so a surface can restyle it as status text", () => {
    render(<Badge variant="warning">Auto-disabled</Badge>);

    const badge = screen.getByText("Auto-disabled");
    expect(badge).toHaveAttribute("data-slot", "badge");
    expect(badge).toHaveAttribute("data-variant", "warning");
  });

  it("reports the default variant when none is given", () => {
    render(<Badge>Ready</Badge>);

    expect(screen.getByText("Ready")).toHaveAttribute("data-variant", "default");
  });
});
