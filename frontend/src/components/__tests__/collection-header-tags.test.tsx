/** Inline collection tags preserve confirmed edits, drafts, and session ownership. */
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import { CollectionHeaderTags } from "../collection-header-tags";
import { renderApp } from "@/test-support/render";
import { retirePrivateSessionScope } from "@/lib/auth-store";

function mount(tags = ["functional"], canEdit = true) {
  const save = vi
    .fn<(tags: string[]) => Promise<string[]>>()
    .mockImplementation(async (tags) => tags);
  renderApp(<CollectionHeaderTags tags={tags} canEdit={canEdit} onSave={save} />);
  return save;
}
async function draft(value: string) {
  await userEvent.click(screen.getByRole("button", { name: "+ Add tag" }));
  await userEvent.type(screen.getByRole("textbox", { name: "New tag" }), value);
}

describe("CollectionHeaderTags", () => {
  it("adds a trimmed tag from the confirmed receipt", async () => {
    const save = mount();
    await draft(" outdoor ");
    await userEvent.keyboard("{Enter}");
    expect(save).toHaveBeenCalledWith(["functional", "outdoor"]);
    expect(await screen.findByRole("button", { name: "Remove tag outdoor" })).toBeVisible();
  });
  it("does not write a duplicate tag", async () => {
    const save = mount();
    await draft(" FUNCTIONAL ");
    await userEvent.keyboard("{Enter}");
    expect(save).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
  });
  it("cancels an inline draft with Escape", async () => {
    const save = mount();
    await draft("outdoor");
    await userEvent.keyboard("{Escape}");
    expect(save).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
    await waitFor(() => expect(screen.getByRole("button", { name: "+ Add tag" })).toHaveFocus());
  });
  it("removes only the chosen tag", async () => {
    const save = mount(["functional", "outdoor"]);
    await userEvent.click(screen.getByRole("button", { name: "Remove tag functional" }));
    expect(save).toHaveBeenCalledWith(["outdoor"]);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Remove tag functional" })).toBeNull(),
    );
  });
  it("withholds edits from a read-only collection", () => {
    mount(["functional"], false);
    expect(screen.getByText("functional")).toBeVisible();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("retains a failed draft for retry", async () => {
    const save = mount();
    save.mockRejectedValueOnce(new Error("offline"));
    await draft("outdoor");
    await userEvent.keyboard("{Enter}");
    expect(await screen.findByRole("alert")).toBeVisible();
    const input = screen.getByRole("textbox", { name: "New tag" });
    expect(input).toHaveValue("outdoor");
    fireEvent.submit(input.closest("form")!);
    expect(await screen.findByRole("button", { name: "Remove tag outdoor" })).toBeVisible();
  });
  it("serializes pending tag writes", async () => {
    const pending = Promise.withResolvers<string[]>();
    const save = mount();
    save.mockReturnValueOnce(pending.promise);
    await draft("outdoor");
    const form = screen.getByRole("textbox").closest("form")!;
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(save).toHaveBeenCalledOnce();
    await act(async () => pending.resolve(["functional", "outdoor"]));
  });
  it("retires an earlier session's editor", async () => {
    const save = mount();
    await draft("outdoor");
    act(() => retirePrivateSessionScope());
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });
  it("keeps confirmed tags in the next edit before parent refresh", async () => {
    const save = mount();
    await draft("outdoor");
    await userEvent.keyboard("{Enter}");
    await screen.findByRole("button", { name: "Remove tag outdoor" });
    await draft("garden");
    await userEvent.keyboard("{Enter}");
    expect(save).toHaveBeenLastCalledWith(["functional", "outdoor", "garden"]);
  });
});
