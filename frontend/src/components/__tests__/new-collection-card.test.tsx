/*
 * The inline "New collection" card.
 *
 * A submit that does nothing leaves the person guessing, so an empty name and a
 * name the server rejects as a duplicate each say why, beside the field.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NewCollectionCard, type CollectionCreateOutcome } from "@/components/new-collection-card";

async function openCard(onCreate: (name: string) => Promise<CollectionCreateOutcome>) {
  const user = userEvent.setup();
  render(<NewCollectionCard maxLength={100} onCreate={onCreate} />);
  await user.click(screen.getByRole("button", { name: "New collection" }));
  return user;
}

describe("NewCollectionCard", () => {
  it("says a name is needed when submitted empty", async () => {
    const onCreate = vi.fn<(name: string) => Promise<CollectionCreateOutcome>>();
    const user = await openCard(onCreate);

    await user.click(screen.getByRole("button", { name: "Create collection" }));

    expect(await screen.findByText("Enter a collection name.")).toBeVisible();
    expect(onCreate).not.toHaveBeenCalled();
  });

  it("shows the reason beside the field when the name is a duplicate", async () => {
    const onCreate = vi.fn<(name: string) => Promise<CollectionCreateOutcome>>().mockResolvedValue({
      status: "invalid",
      message: 'A collection named "Boats" already exists here.',
    });
    const user = await openCard(onCreate);

    await user.type(screen.getByRole("textbox"), "Boats");
    await user.click(screen.getByRole("button", { name: "Create collection" }));

    expect(
      await screen.findByText('A collection named "Boats" already exists here.'),
    ).toBeVisible();
    expect(screen.getByRole("textbox")).toBeInvalid();
  });
});
