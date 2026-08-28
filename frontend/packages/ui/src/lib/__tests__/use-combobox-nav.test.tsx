/*
 * Keyboard navigation for every combobox in the product — the tag picker, the
 * collection picker, the printer selector.
 *
 * Two things make this worth testing directly rather than through one of those
 * components.
 *
 * The list shrinks as the user types, so a stored highlight routinely outruns it.
 * `useComboboxNav` clamps during render rather than correcting in an effect, and a
 * regression there is an out-of-range index handed to `onSelect` — the user picks the
 * third suggestion and gets nothing, or something else.
 *
 * And the ARIA it returns is the whole of the accessibility contract:
 * `aria-activedescendant` is how a screen reader announces the highlighted option, and
 * an `undefined` where an id belongs is silence rather than a visible bug. So the
 * tests drive a real input and assert on the listbox the hook describes, not on the
 * index it happens to store.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useComboboxNav } from "../use-combobox-nav";

interface ComboboxProps {
  count: number;
  onSelect?: (index: number) => void;
  onCommitInput?: () => void;
  onClose?: () => void;
  label?: string;
}

/** A combobox wired the way the product wires one: input, listbox, option ids. */
function Combobox({ count, onSelect, onCommitInput, onClose, label = "Search" }: ComboboxProps) {
  const nav = useComboboxNav(count, {
    onSelect: onSelect ?? (() => {}),
    onCommitInput,
    onClose,
  });
  return (
    <>
      <input {...nav.inputProps} aria-label={label} />
      <ul id={nav.listboxId} role="listbox" aria-label={`${label} options`}>
        {Array.from({ length: count }, (_, index) => (
          <li
            key={index}
            id={nav.optionId(index)}
            role="option"
            aria-selected={index === nav.activeIndex}
          >
            Option {index + 1}
          </li>
        ))}
      </ul>
    </>
  );
}

function input(label = "Search") {
  return screen.getByRole("combobox", { name: label });
}

/** Press a key on the input; the return says whether the hook took the key over. */
function press(key: string, label = "Search") {
  return !fireEvent.keyDown(input(label), { key });
}

/** The option the hook currently reports as highlighted, or `null` for none. */
function highlighted() {
  return screen.queryByRole("option", { selected: true });
}

describe("useComboboxNav", () => {
  describe("arrow navigation", () => {
    it("highlights the first option on the way down from nothing", () => {
      render(<Combobox count={3} />);

      press("ArrowDown");

      expect(highlighted()).toHaveTextContent("Option 1");
    });

    it("wraps past the last option back to the first", () => {
      render(<Combobox count={3} />);
      press("ArrowDown");
      press("ArrowDown");
      press("ArrowDown");

      press("ArrowDown");

      expect(highlighted()).toHaveTextContent("Option 1");
    });

    it("wraps below the first option round to the last", () => {
      render(<Combobox count={3} />);
      press("ArrowDown");

      press("ArrowUp");

      expect(highlighted()).toHaveTextContent("Option 3");
    });

    it("takes over the arrow key so the caret does not move", () => {
      // Without preventDefault the text cursor jumps within the input while the
      // highlight moves, which is the classic broken-combobox feel.
      render(<Combobox count={3} />);

      expect(press("ArrowDown")).toBe(true);
    });

    it("ignores the arrow keys when there is nothing to highlight", () => {
      render(<Combobox count={0} />);

      const prevented = press("ArrowDown");

      expect(prevented).toBe(false);
      expect(highlighted()).toBeNull();
    });

    it("clamps a highlight the shrinking list has outrun", () => {
      // Typing narrows the suggestions under a highlight that was valid a keystroke
      // ago. Clamped during render, so `onSelect` can never receive an index past the
      // end of the list the user is looking at.
      const { rerender } = render(<Combobox count={5} />);
      press("ArrowDown");
      press("ArrowDown");
      press("ArrowDown");

      rerender(<Combobox count={2} />);

      expect(highlighted()).toHaveTextContent("Option 2");
    });
  });

  describe("Enter", () => {
    it("selects the highlighted option", () => {
      const onSelect = vi.fn<(index: number) => void>();
      render(<Combobox count={3} onSelect={onSelect} />);
      press("ArrowDown");

      press("Enter");

      expect(onSelect).toHaveBeenCalledWith(0);
    });

    it("clears the highlight once a selection is made", () => {
      // The list is about to be replaced by the result of the selection, so a highlight
      // left behind would point into the next list.
      render(<Combobox count={3} />);
      press("ArrowDown");

      press("Enter");

      expect(highlighted()).toBeNull();
    });

    it("commits the typed text when nothing is highlighted", () => {
      // Typing a brand-new tag and pressing Enter: there is no option to select, and
      // the input's own value is the answer.
      const onCommitInput = vi.fn<() => void>();
      render(<Combobox count={3} onCommitInput={onCommitInput} />);

      press("Enter");

      expect(onCommitInput).toHaveBeenCalledTimes(1);
    });

    it("leaves Enter alone when nothing is highlighted and free text is not allowed", () => {
      // No `onCommitInput` means the caller only accepts existing options, so Enter has
      // to fall through to the form rather than being swallowed.
      render(<Combobox count={3} />);

      expect(press("Enter")).toBe(false);
    });
  });

  describe("Escape", () => {
    it("closes the list", () => {
      const onClose = vi.fn<() => void>();
      render(<Combobox count={3} onClose={onClose} />);

      press("Escape");

      expect(onClose).toHaveBeenCalledTimes(1);
    });

    it("clears the highlight even when the caller wants no close handler", () => {
      render(<Combobox count={3} />);
      press("ArrowDown");

      press("Escape");

      expect(highlighted()).toBeNull();
    });
  });

  describe("inputProps", () => {
    it("announces the list as expanded when it has options", () => {
      render(<Combobox count={3} />);

      expect(input()).toHaveAttribute("aria-expanded", "true");
    });

    it("announces the list as collapsed when it is empty", () => {
      render(<Combobox count={0} />);

      expect(input()).toHaveAttribute("aria-expanded", "false");
    });

    it("points aria-activedescendant at the highlighted option", () => {
      // This is the whole of the screen-reader contract: the id here is how the
      // highlighted option gets announced at all.
      render(<Combobox count={3} />);

      press("ArrowDown");

      expect(input()).toHaveAttribute("aria-activedescendant", highlighted()!.id);
    });

    it("omits aria-activedescendant while nothing is highlighted", () => {
      render(<Combobox count={3} />);

      expect(input()).not.toHaveAttribute("aria-activedescendant");
    });

    it("names the listbox it controls", () => {
      render(<Combobox count={3} />);

      expect(input()).toHaveAttribute(
        "aria-controls",
        screen.getByRole("listbox", { name: "Search options" }).id,
      );
    });

    it("asks for list-style autocompletion", () => {
      render(<Combobox count={3} />);

      expect(input()).toHaveAttribute("aria-autocomplete", "list");
    });
  });

  describe("optionId", () => {
    it("gives two comboboxes on one page distinct option ids", () => {
      // Duplicated ids would make `aria-activedescendant` resolve to whichever element
      // the browser found first, so the wrong option gets announced.
      render(
        <>
          <Combobox count={3} label="Tags" />
          <Combobox count={3} label="Collections" />
        </>,
      );

      const [first, second] = screen.getAllByRole("option", { name: "Option 1" });
      expect(first.id).not.toBe(second.id);
    });
  });
});
