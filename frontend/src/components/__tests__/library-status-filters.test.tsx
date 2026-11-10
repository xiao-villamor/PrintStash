/** Status controls follow the prototype without advertising an unsupported server filter. */
import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { LibraryStatusFilters } from "../library-status-filters";
import { renderApp } from "@/test-support/render";

function Statuses() {
  const [value, setValue] = useState<string[]>([]);
  return (
    <LibraryStatusFilters
      value={value}
      counts={[
        { value: "known_good", count: 3 },
        { value: "needs_test", count: 1 },
      ]}
      total={6}
      onChange={setValue}
      onIntent={() => {}}
    />
  );
}
describe("LibraryStatusFilters", () => {
  it("selects one status with arrow navigation", () => {
    renderApp(<Statuses />);
    const all = screen.getByRole("radio", { name: "All 6" });
    all.focus();
    fireEvent.keyDown(all, { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "Ready 3" })).toBeChecked();
    expect(all).not.toBeChecked();
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(screen.getByRole("radio", { name: "Needs test 1" })).toHaveFocus();
  });
  it("retains an active empty status", () => {
    renderApp(
      <LibraryStatusFilters
        value={["failed"]}
        total={0}
        counts={[
          { value: "known_good", count: 0 },
          { value: "failed", count: 0 },
        ]}
        onChange={() => {}}
        onIntent={() => {}}
      />,
    );
    expect(screen.queryByRole("radio", { name: /Ready/ })).toBeNull();
    expect(screen.getByRole("radio", { name: "Failed 0" })).toBeChecked();
  });
  it("does not invent a No G-code filter or count", () => {
    const change = vi.fn<(value: string[]) => void>();
    renderApp(
      <LibraryStatusFilters
        value={[]}
        counts={undefined}
        total={6}
        onChange={change}
        onIntent={() => {}}
      />,
    );
    const unavailable = screen.getByRole("radio", { name: "No G-code" });
    expect(unavailable).toBeDisabled();
    fireEvent.click(unavailable);
    expect(change).not.toHaveBeenCalled();
  });
});
