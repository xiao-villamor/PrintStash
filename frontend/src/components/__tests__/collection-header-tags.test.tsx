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
  const opener = screen.queryByRole("button", { name: "+ Add tag" });
  if (opener) await userEvent.click(opener);
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
    expect(screen.getByRole("textbox")).toHaveValue(" FUNCTIONAL ");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "#functional is already on this collection.",
    );
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
  it("normalizes a tag submitted with comma", async () => {
    const save = mount();
    await draft("#Diseño 3D,");
    await waitFor(() => expect(screen.getByRole("textbox")).toHaveValue(""));
    expect(save).toHaveBeenCalledWith(["functional", "diseño-3d"]);
    expect(screen.getByRole("textbox")).toHaveFocus();
  });
  it("keeps an empty submission available for correction", async () => {
    const save = mount();
    await userEvent.click(screen.getByRole("button", { name: "+ Add tag" }));
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("alert")).toHaveTextContent("Type a tag name first.");
    expect(save).not.toHaveBeenCalled();
  });
  it("retains an unsaved tag when focus leaves", async () => {
    const save = mount();
    await draft("outdoor");
    await userEvent.tab();
    expect(screen.getByRole("textbox")).toHaveValue("outdoor");
    expect(screen.getByRole("textbox")).not.toHaveFocus();
    expect(save).not.toHaveBeenCalled();
  });
  it("closes an empty field without stealing the next control's focus", async () => {
    mount();
    await userEvent.click(screen.getByRole("button", { name: "+ Add tag" }));
    const next = document.createElement("button");
    document.body.append(next);
    try {
      await userEvent.click(next);
      expect(screen.queryByRole("textbox")).toBeNull();
      await new Promise((resolve) => requestAnimationFrame(resolve));
      expect(next).toHaveFocus();
    } finally {
      next.remove();
    }
  });
  it("removes the last tag with Backspace on an empty field", async () => {
    const save = mount(["functional", "outdoor"]);
    await userEvent.click(screen.getByRole("button", { name: "+ Add tag" }));
    await userEvent.keyboard("{Backspace}");
    expect(save).toHaveBeenCalledWith(["functional"]);
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Remove tag outdoor" })).toBeNull(),
    );
    expect(screen.getByRole("textbox")).toHaveFocus();
  });
  it("keeps composition Enter out of tag submission", async () => {
    const save = mount();
    await draft("庭");
    const input = screen.getByRole("textbox");
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, { key: "Enter", isComposing: true });
    fireEvent.submit(input.closest("form")!);
    expect(save).not.toHaveBeenCalled();
    fireEvent.compositionEnd(input);
    await userEvent.keyboard("{Enter}");
    expect(save).toHaveBeenCalledWith(["functional", "庭"]);
  });
  it("bounds the tag draft to the prototype's 32 characters", async () => {
    mount();
    await draft("a".repeat(33));
    expect(screen.getByRole("textbox")).toHaveValue("a".repeat(32));
  });
  it("closes an empty validation error on blur", async () => {
    const save = mount();
    await userEvent.click(screen.getByRole("button", { name: "+ Add tag" }));
    await userEvent.keyboard("{Enter}");
    expect(screen.getByRole("alert")).toBeVisible();
    await userEvent.tab();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(save).not.toHaveBeenCalled();
  });
});
