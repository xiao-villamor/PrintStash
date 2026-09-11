/** Family Choices preserve explicit selection in a draft until Multipart save. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyChoicesDialog } from "../choices-dialog";
import { aModel, aModelListItem } from "@/test-support/factories";
import { aFamilyMember, aFamilySummary } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());
const first = aFamilyMember();
const second = aFamilyMember({
  id: 12,
  model_id: 2,
  role: "print_variant",
  model: aModelListItem({ id: 2, name: "Spatula" }),
});
const base = { "GET /api/v1/models/1": json(aModel({ family: aFamilySummary() })) };

describe("Family Choices picker", () => {
  it("preserves explicit selection across member searches", async () => {
    const user = userEvent.setup();
    const select = vi.fn<(members: ReturnType<typeof aFamilyMember>[]) => void>();
    const close = vi.fn<() => void>();
    const { requestsWithMethod } = renderApp(
      <FamilyChoicesDialog modelId={1} usedIds={new Set()} onSelect={select} onClose={close} />,
      {
        routes: {
          ...base,
          "GET /api/v1/families/7/members": (url) =>
            json({
              items: url.includes("q=spatula") ? [second] : [first],
              total: 1,
              next_cursor: null,
            }),
        },
      },
    );
    await user.click(await screen.findByRole("checkbox", { name: "Select Benchy" }));
    await user.type(screen.getByRole("textbox", { name: "Search your library…" }), "spatula");
    await user.click(await screen.findByRole("checkbox", { name: "Select Spatula" }));
    await user.click(screen.getByRole("button", { name: "Add 2 Choices" }));
    expect(select).toHaveBeenCalledWith([first, second]);
    expect(close).toHaveBeenCalledOnce();
    expect(requestsWithMethod("POST")).toEqual([]);
    expect(requestsWithMethod("PUT")).toEqual([]);
  });

  it("keeps existing Choices unavailable while paging", async () => {
    const user = userEvent.setup();
    renderApp(
      <FamilyChoicesDialog
        modelId={1}
        usedIds={new Set([1])}
        onSelect={() => {}}
        onClose={() => {}}
      />,
      {
        routes: {
          ...base,
          "GET /api/v1/families/7/members": (url) =>
            json(
              url.includes("cursor=next")
                ? { items: [second], total: 2, next_cursor: null }
                : { items: [first], total: 2, next_cursor: "next" },
            ),
        },
      },
    );
    expect(await screen.findByRole("checkbox", { name: "Select Benchy" })).toBeDisabled();
    expect(screen.getByText("Already used in this multipart model")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("checkbox", { name: "Select Spatula" })).toBeEnabled();
  });

  it("recovers a failed member read", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderApp(
      <FamilyChoicesDialog
        modelId={1}
        usedIds={new Set()}
        onSelect={() => {}}
        onClose={() => {}}
      />,
      {
        routes: {
          ...base,
          "GET /api/v1/families/7/members": () =>
            failed
              ? json({ detail: "family_not_found" }, 404)
              : json({ items: [first], total: 1, next_cursor: null }),
        },
      },
    );
    expect(await screen.findByRole("alert")).toBeVisible();
    failed = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("checkbox", { name: "Select Benchy" })).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });

  it("explains when the source Model has no Family", async () => {
    renderApp(
      <FamilyChoicesDialog
        modelId={1}
        usedIds={new Set()}
        onSelect={() => {}}
        onClose={() => {}}
      />,
      {
        routes: { "GET /api/v1/models/1": json(aModel({ family: null })) },
      },
    );
    expect(await screen.findByText("This Model does not belong to a Family yet.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add 0 Choices" })).toBeDisabled();
  });
});
