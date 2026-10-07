/** Shared tag editing preserves selections and scopes completion to its visible editor. */
import "@testing-library/jest-dom/vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EntityTagsEditor } from "../entity-tags-editor";

const props = {
  entityLabel: "Parts",
  tags: ["Local"],
  availableTags: [],
  help: "Direct tags",
  open: true,
};

describe("EntityTagsEditor", () => {
  it("retains ordinary tags after a rejected submission", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn<() => void>();
    render(
      <EntityTagsEditor
        {...props}
        onClose={onClose}
        onSave={async () => {
          throw new Error("Reported by the owner");
        }}
      />,
    );
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    expect(screen.getByTitle("Remove Local")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save tags" })).toBeEnabled();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("submits ordinary tags once per pending command", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<void>();
    const save = vi.fn<(tags: string[], signal: AbortSignal) => Promise<void>>(() => held.promise);
    render(<EntityTagsEditor {...props} onClose={() => {}} onSave={save} />);
    await user.dblClick(screen.getByRole("button", { name: "Save tags" }));
    expect(save).toHaveBeenCalledTimes(1);
    expect(save).toHaveBeenCalledWith(["Local"], expect.any(AbortSignal));
    expect(screen.getByTitle("Remove Local")).toBeDisabled();
    await act(async () => held.resolve());
    await waitFor(() => expect(screen.getByRole("button", { name: "Save tags" })).toBeEnabled());
  });

  it("suppresses a retired tag editor close callback", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<void>();
    const onClose = vi.fn<() => void>();
    const view = render(
      <EntityTagsEditor {...props} onClose={onClose} onSave={() => held.promise} />,
    );
    await user.click(screen.getByRole("button", { name: "Save tags" }));
    view.unmount();
    await act(async () => held.resolve());
    expect(onClose).not.toHaveBeenCalled();
  });
});
