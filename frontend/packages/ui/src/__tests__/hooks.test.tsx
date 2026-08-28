import { act, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { useComboboxNav } from "../lib/use-combobox-nav";
import { useMediaQuery } from "../lib/use-media-query";

afterEach(() => vi.unstubAllGlobals());

interface ComboboxHarnessProps {
  count: number;
  onSelect: (index: number) => void;
  onCommitInput: () => void;
  onClose: () => void;
}

function ComboboxHarness(props: ComboboxHarnessProps) {
  const navigation = useComboboxNav(props.count, props);
  return (
    <>
      <input aria-label="Options" {...navigation.inputProps} />
      <output aria-label="Active index">{navigation.activeIndex ?? "none"}</output>
      <span>{navigation.optionId(0)}</span>
      <button type="button" onClick={() => navigation.setActiveIndex(1)}>
        Set active
      </button>
    </>
  );
}

it("selects, commits, closes, and clamps combobox navigation", () => {
  const onSelect = vi.fn<(index: number) => void>();
  const onCommitInput = vi.fn<() => void>();
  const onClose = vi.fn<() => void>();
  const { rerender } = render(
    <ComboboxHarness
      count={2}
      onSelect={onSelect}
      onCommitInput={onCommitInput}
      onClose={onClose}
    />,
  );
  const input = screen.getByRole("combobox", { name: "Options" });
  fireEvent.keyDown(input, { key: "ArrowDown" });
  expect(screen.getByRole("status", { name: "Active index" })).toHaveTextContent("0");
  fireEvent.keyDown(input, { key: "ArrowUp" });
  expect(screen.getByRole("status", { name: "Active index" })).toHaveTextContent("1");
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onSelect).toHaveBeenCalledWith(1);
  fireEvent.keyDown(input, { key: "Enter" });
  expect(onCommitInput).toHaveBeenCalledOnce();
  fireEvent.keyDown(input, { key: "Escape" });
  expect(onClose).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Set active" }));
  rerender(
    <ComboboxHarness
      count={1}
      onSelect={onSelect}
      onCommitInput={onCommitInput}
      onClose={onClose}
    />,
  );
  expect(screen.getByRole("status", { name: "Active index" })).toHaveTextContent("0");
  expect(screen.getByText(/-opt-0$/)).toBeInTheDocument();
});

it("subscribes to matchMedia and updates when the query changes", () => {
  let matches = false;
  let listener: (() => void) | undefined;
  const removeEventListener = vi.fn<(name: string, listener: () => void) => void>();
  vi.stubGlobal("matchMedia", () => ({
    get matches() {
      return matches;
    },
    addEventListener: (_name: string, next: () => void) => {
      listener = next;
    },
    removeEventListener,
  }));
  const { result, unmount } = renderHook(() => useMediaQuery("(min-width: 40rem)"));
  expect(result.current).toBe(false);
  act(() => {
    matches = true;
    listener?.();
  });
  expect(result.current).toBe(true);
  unmount();
  expect(removeEventListener).toHaveBeenCalledOnce();
});
