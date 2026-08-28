import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";

import { Checkbox } from "../components/checkbox";
import { Drawer } from "../components/drawer";
import { DropdownMenu } from "../components/dropdown-menu";
import { TabBar } from "../components/tabs";

it("toggles a checkbox without bubbling to its parent", () => {
  const onChange = vi.fn<(checked: boolean) => void>();
  const parentClick = vi.fn<() => void>();
  render(
    <div onClick={parentClick}>
      <Checkbox checked={false} onChange={onChange} ariaLabel="Select model" />
    </div>,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "Select model" }));
  expect(onChange).toHaveBeenCalledWith(true);
  expect(parentClick).not.toHaveBeenCalled();
});

it("moves menu focus with arrows and dismisses outside", async () => {
  const onOpenChange = vi.fn<(open: boolean) => void>();
  render(
    <DropdownMenu
      open
      onOpenChange={onOpenChange}
      align="start"
      trigger={<button data-menu-trigger>Open</button>}
    >
      <button role="menuitem">First</button>
      <button role="menuitem">Second</button>
    </DropdownMenu>,
  );
  await act(async () => {
    await new Promise((resolve) => requestAnimationFrame(resolve));
  });
  const first = screen.getByRole("menuitem", { name: "First" });
  fireEvent.keyDown(first, { key: "ArrowDown" });
  const second = screen.getByRole("menuitem", { name: "Second" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(second, { key: "Home" });
  expect(first).toHaveFocus();
  fireEvent.keyDown(first, { key: "ArrowUp" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(second, { key: "End" });
  expect(second).toHaveFocus();
  fireEvent.keyDown(second, { key: "Escape" });
  expect(screen.getByRole("button", { name: "Open" })).toHaveFocus();
  fireEvent.pointerDown(document.body);
  expect(onOpenChange).toHaveBeenCalledWith(false);
});

it("cycles tabs from the keyboard and click", () => {
  const onChange = vi.fn<(key: string) => void>();
  render(
    <TabBar
      tabs={[
        { key: "one", label: "One" },
        { key: "two", label: "Two" },
      ]}
      active="one"
      onChange={onChange}
      showIndicator={false}
    />,
  );
  fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowRight" });
  fireEvent.click(screen.getByRole("tab", { name: "Two" }));
  expect(onChange).toHaveBeenNthCalledWith(1, "two");
  expect(onChange).toHaveBeenNthCalledWith(2, "two");
  fireEvent.keyDown(screen.getByRole("tablist"), { key: "ArrowLeft" });
  expect(onChange).toHaveBeenNthCalledWith(3, "two");
});

it("renders both drawer sides and dismisses from the backdrop", () => {
  const onClose = vi.fn<() => void>();
  const { rerender } = render(
    <Drawer open onClose={onClose} side="left" ariaLabel="Filters">
      Contents
    </Drawer>,
  );
  const dialog = screen.getByRole("dialog", { name: "Filters" });
  expect(dialog).toHaveClass("left-0");
  fireEvent.click(dialog.previousElementSibling!);
  expect(onClose).toHaveBeenCalledOnce();
  rerender(
    <Drawer open onClose={onClose} side="bottom" ariaLabel="Filters">
      Contents
    </Drawer>,
  );
  expect(screen.getByRole("dialog", { name: "Filters" })).toHaveClass("bottom-0");
});

it("keeps dialog-dropdown navigation inside its picker", () => {
  render(
    <DropdownMenu
      open
      role="dialog"
      onOpenChange={() => {}}
      trigger={<button data-menu-trigger>Open picker</button>}
    >
      <input aria-label="Search" />
    </DropdownMenu>,
  );
  const input = screen.getByRole("textbox", { name: "Search" });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(input).toBeInTheDocument();
});
