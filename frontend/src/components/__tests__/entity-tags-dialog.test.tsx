/*
 * The shared entity tag editor must preserve the complete direct tag set and
 * hide mutations when the caller has read-only access.
 */
import "@testing-library/jest-dom/vitest";

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EntityTagsDialog } from "@/components/entity-tags-dialog";

describe("EntityTagsDialog", () => {
  it("persists a changed direct tag set", async () => {
    const onSave = vi.fn<(tags: string[]) => Promise<void>>().mockResolvedValue();
    render(
      <EntityTagsDialog
        entityLabel="Parts"
        tags={["Existing"]}
        availableTags={[
          { id: 1, name: "Existing", slug: "existing", model_count: 1 },
          { id: 2, name: "Workshop", slug: "workshop", model_count: 2 },
        ]}
        canEdit
        help="Inherited by descendants."
        onSave={onSave}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "Edit tags" }));
    expect(await screen.findByText("Inherited by descendants.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Workshop" }));
    await userEvent.type(screen.getByLabelText("Tags to add"), "Painted{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Save tags" }));

    expect(onSave).toHaveBeenCalledWith(
      ["Existing", "Workshop", "Painted"],
      expect.any(AbortSignal),
    );
  });

  it("freezes the tag command with the selection opened for editing", async () => {
    const original = vi.fn<(tags: string[]) => Promise<void>>().mockResolvedValue();
    const refreshed = vi.fn<(tags: string[]) => Promise<void>>().mockResolvedValue();
    const props = {
      entityLabel: "Parts",
      tags: ["Original"],
      availableTags: [],
      canEdit: true,
      help: "Direct tags",
      triggerMode: "command" as const,
    };
    const view = render(<EntityTagsDialog {...props} onSave={original} />);
    await userEvent.click(screen.getByRole("button", { name: "Edit tags" }));
    await screen.findByRole("button", { name: "Save tags" });

    view.rerender(<EntityTagsDialog {...props} tags={["Refreshed"]} onSave={refreshed} />);
    await userEvent.type(screen.getByLabelText("Tags to add"), "My tag{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Save tags" }));

    expect(original).toHaveBeenCalledWith(["Original", "My tag"], expect.any(AbortSignal));
    expect(refreshed).not.toHaveBeenCalled();
  });

  it("does not expose editing without permission", () => {
    render(
      <EntityTagsDialog
        entityLabel="Read only"
        tags={["Visible"]}
        availableTags={[]}
        canEdit={false}
        triggerMode="command"
        help="Read only"
        onSave={async () => {}}
      />,
    );

    expect(screen.getByText("Visible")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit tags" })).not.toBeInTheDocument();
  });

  it("captures an initially opened edit intent", async () => {
    const original = vi.fn<(tags: string[]) => Promise<void>>().mockResolvedValue();
    const refreshed = vi.fn<(tags: string[]) => Promise<void>>().mockResolvedValue();
    const view = render(
      <EntityTagsDialog
        entityLabel="Parts"
        tags={["Original"]}
        availableTags={[]}
        canEdit
        help="Direct tags"
        onSave={original}
        initialOpen
        triggerMode="none"
      />,
    );
    await screen.findByRole("button", { name: "Save tags" });
    view.rerender(
      <EntityTagsDialog
        entityLabel="Parts"
        tags={["Refreshed"]}
        availableTags={[]}
        canEdit
        help="Direct tags"
        onSave={refreshed}
        initialOpen
        triggerMode="none"
      />,
    );
    await userEvent.type(screen.getByLabelText("Tags to add"), "My draft{Enter}");
    await userEvent.click(screen.getByRole("button", { name: "Save tags" }));
    expect(original).toHaveBeenCalledWith(["Original", "My draft"], expect.any(AbortSignal));
    expect(refreshed).not.toHaveBeenCalled();
  });

  it("retires an open editor when permission is removed", async () => {
    const onSave = vi.fn<(tags: string[]) => Promise<void>>().mockResolvedValue();
    const view = render(
      <EntityTagsDialog
        entityLabel="Parts"
        tags={["Original"]}
        availableTags={[]}
        canEdit
        help="Direct tags"
        onSave={onSave}
        initialOpen
        triggerMode="none"
      />,
    );
    await screen.findByRole("button", { name: "Save tags" });
    view.rerender(
      <EntityTagsDialog
        entityLabel="Parts"
        tags={["Original"]}
        availableTags={[]}
        canEdit={false}
        help="Direct tags"
        onSave={onSave}
        initialOpen
        triggerMode="none"
      />,
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    view.rerender(
      <EntityTagsDialog
        entityLabel="Parts"
        tags={["Original"]}
        availableTags={[]}
        canEdit
        help="Direct tags"
        onSave={onSave}
        initialOpen
        triggerMode="none"
      />,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });
});
