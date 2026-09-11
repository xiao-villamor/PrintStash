import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyTrashDialog } from "../trash-dialog";
import { aFamily } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());
const family = aFamily({ deleted_at: "2026-09-10T12:00:00Z" });

describe("Family trash", () => {
  it("confirms a versioned Family purge without deleting member Models", async () => {
    const user = userEvent.setup();
    let purged = false;
    const { requestsWithMethod } = renderApp(<FamilyTrashDialog onClose={() => {}} />, {
      routes: {
        "GET /api/v1/families": () =>
          json({ items: purged ? [] : [family], total: purged ? 0 : 1 }),
        "DELETE /api/v1/families/7/purge": () => {
          purged = true;
          return new Response(null, { status: 204 });
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Delete Family permanently" }));
    const confirm = screen.getByRole("dialog", { name: "Delete Family permanently" });
    expect(within(confirm).getByText(/Models, files and print history will remain/)).toBeVisible();
    expect(requestsWithMethod("DELETE")).toHaveLength(0);
    await user.click(within(confirm).getByRole("button", { name: "Delete Family permanently" }));
    await waitFor(() => expect(requestsWithMethod("DELETE")).toHaveLength(1));
    expect(requestsWithMethod("DELETE")[0].url).toContain("/families/7/purge?version=3");
    await waitFor(() => expect(screen.queryByText("Benchy variations")).not.toBeInTheDocument());
  });

  it("keeps a restore conflict visible without moving or deleting Models", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(<FamilyTrashDialog onClose={() => {}} />, {
      routes: {
        "GET /api/v1/families": json({ items: [family], total: 1 }),
        "POST /api/v1/families/7/restore": json({ detail: "family_restore_conflict" }, 409),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Restore Family" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "reserved member now belongs to another Family",
    );
    expect(screen.getByText("Benchy variations")).toBeVisible();
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({ version: 3 });
    expect(requestsWithMethod("DELETE")).toHaveLength(0);
  });
});
