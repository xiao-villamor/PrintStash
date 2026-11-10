/** Shared commands keep native editing, modal ownership and session isolation. */
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { KeyboardShortcuts } from "@/components/keyboard-shortcuts";
import {
  CollectionsPaneProvider,
  CollectionsPaneHost,
  LibraryCollectionsPane,
} from "@/components/collections-pane";
import { useCollectionsPane } from "@/lib/collections-pane-context";
import { Modal } from "@/components/ui/modal";
import { renderApp } from "@/test-support/render";
import {
  cancelCollectionRename,
  consumeCollectionRename,
  keyboardOverlayOpen,
  modalOverlayOpen,
  pendingCollectionRename,
  requestCollectionRename,
} from "@/lib/keyboard-commands";
import { clearLogin } from "@/lib/auth-store";
import { toast, undoLastChange } from "@/lib/toast";

function Controls() {
  const pane = useCollectionsPane();
  return (
    <header data-current-collection="parts">
      <button
        type="button"
        aria-controls="collections-pane"
        aria-expanded={pane?.open}
        onClick={pane?.toggle}
      >
        Toggle sidebar
      </button>
      <input aria-label="Global search" />
    </header>
  );
}
function renderShortcuts({ modal = false }: { modal?: boolean } = {}) {
  return renderApp(
    <CollectionsPaneProvider>
      <Controls />
      <CollectionsPaneHost />
      <LibraryCollectionsPane>
        <button type="button" role="treeitem" data-outliner-collection="parts" aria-selected="true">
          Parts
        </button>
      </LibraryCollectionsPane>
      <KeyboardShortcuts />
      {modal && (
        <Modal open onClose={() => {}} title="Edit Model">
          <input aria-label="Model name" />
        </Modal>
      )}
    </CollectionsPaneProvider>,
  );
}
function command(code: string, modifiers: KeyboardEventInit = {}) {
  fireEvent.keyDown(document.activeElement ?? window, {
    code,
    bubbles: true,
    cancelable: true,
    ...modifiers,
  });
}
beforeEach(() => {
  localStorage.clear();
  cancelCollectionRename();
});
afterEach(() => cancelCollectionRename());

describe("KeyboardShortcuts", () => {
  it("shows the authoritative command catalogue", async () => {
    renderShortcuts();
    await userEvent.keyboard("?");
    const dialog = screen.getByRole("dialog", { name: "Keyboard shortcuts" });
    for (const name of ["Find and move around", "Models", "Print results"])
      expect(within(dialog).getByRole("heading", { name })).toBeVisible();
    for (const name of [
      "Search panel",
      "Collections panel",
      "Show or hide the sidebar",
      "Show or hide details",
      "Move in the collection tree",
      "Close details, then clear the selection",
      "Rename this collection",
      "Undo the last change",
      "Show this list",
      "Choose a revision",
      "Log a print that came out OK",
      "Log a failed print, with a note",
      "Save the failure note",
    ])
      expect(within(dialog).getByText(name)).toBeVisible();
  });
  it("switches to Search from a focused input", async () => {
    renderShortcuts();
    screen.getByRole("textbox", { name: "Global search" }).focus();
    command("KeyF", { key: "F", ctrlKey: true, shiftKey: true });
    await waitFor(() =>
      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveFocus(),
    );
  });
  it("focuses the collection tree rather than its filter", async () => {
    renderShortcuts();
    screen.getByRole("textbox", { name: "Global search" }).focus();
    command("KeyE", { key: "E", ctrlKey: true, shiftKey: true });
    await waitFor(() => expect(screen.getByRole("treeitem", { name: "Parts" })).toHaveFocus());
  });
  it("closes the sidebar from its focused search input", async () => {
    renderShortcuts();
    command("KeyF", { key: "F", ctrlKey: true, shiftKey: true });
    await waitFor(() =>
      expect(screen.getByRole("searchbox", { name: "Search library" })).toHaveFocus(),
    );
    command("KeyB", { key: "b", ctrlKey: true });
    expect(screen.getByRole("button", { name: "Toggle sidebar" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
    expect(screen.getByRole("button", { name: "Toggle sidebar" })).toHaveFocus();
  });
  it.each([
    { label: "composition", modifiers: { isComposing: true } },
    { label: "alternate modifiers", modifiers: { ctrlKey: true, altKey: true } },
  ])("leaves $label to the input", ({ modifiers }) => {
    renderShortcuts();
    const input = screen.getByRole("textbox", { name: "Global search" });
    input.focus();
    command("KeyF", { key: "F", ctrlKey: true, shiftKey: true, ...modifiers });
    expect(input).toHaveFocus();
    expect(screen.queryByRole("searchbox", { name: "Search library" })).not.toBeInTheDocument();
  });
  it("keeps pane commands inside the current modal", () => {
    renderShortcuts({ modal: true });
    command("KeyF", { key: "F", ctrlKey: true, shiftKey: true });
    expect(screen.queryByRole("searchbox", { name: "Search library" })).not.toBeInTheDocument();
    expect(screen.getByRole("dialog", { name: "Edit Model" })).toHaveFocus();
  });
  it("preserves AltGraph character entry", () => {
    renderShortcuts();
    const input = screen.getByRole("textbox", { name: "Global search" });
    input.focus();
    const event = new KeyboardEvent("keydown", {
      key: "F",
      code: "KeyF",
      ctrlKey: true,
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    });
    Object.defineProperty(event, "getModifierState", {
      value: (modifier: string) => modifier === "AltGraph",
    });
    fireEvent(input, event);
    expect(input).toHaveFocus();
    expect(screen.queryByRole("searchbox", { name: "Search library" })).not.toBeInTheDocument();
  });
  it("preserves native undo while editing text", async () => {
    renderShortcuts();
    let calls = 0;
    toast.undo("Moved Model", () => {
      calls += 1;
    });
    await screen.findByRole("button", { name: "Undo" });
    screen.getByRole("textbox", { name: "Global search" }).focus();
    command("KeyZ", { key: "z", ctrlKey: true });
    expect(calls).toBe(0);
    expect(undoLastChange()).toBe(true);
    await waitFor(() => expect(calls).toBe(1));
  });
  it("toggles help with question mark", async () => {
    renderShortcuts();
    await userEvent.keyboard("?");
    await userEvent.keyboard("?");
    expect(modalOverlayOpen()).toBe(false);
  });
});

describe("collection rename requests", () => {
  it("retains the requested exact collection for its owner", () => {
    renderShortcuts();
    requestCollectionRename("parts");
    expect(consumeCollectionRename("another")).toBe(false);
    expect(consumeCollectionRename("parts")).toBe(true);
    expect(pendingCollectionRename()).toBeNull();
  });
  it("retires a previous session's pending rename", () => {
    renderShortcuts();
    requestCollectionRename("parts");
    clearLogin();
    expect(pendingCollectionRename()).toBeNull();
  });
});

describe("keyboardOverlayOpen", () => {
  it("blocks commands during the first entry frame", () => {
    renderShortcuts({ modal: true });
    expect(modalOverlayOpen()).toBe(true);
    expect(keyboardOverlayOpen()).toBe(true);
  });
  it("releases command ownership before the exit animation ends", async () => {
    renderShortcuts();
    await userEvent.keyboard("?");
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("dialog", { name: "Keyboard shortcuts" })).toHaveAttribute(
      "data-overlay-open",
      "false",
    );
    expect(modalOverlayOpen()).toBe(false);
  });
});
