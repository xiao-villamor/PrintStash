/** A Model joins only the explicitly selected Family with current edit authority. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { JoinFamilyDialog } from "../join-dialog";
import { aFamily, aFamilyMember } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());

describe("Join Family", () => {
  it("joins the selected Family with its current version", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <JoinFamilyDialog modelId={9} onClose={() => {}} onJoined={() => {}} />,
      {
        routes: {
          "GET /api/v1/families": json({ items: [aFamily()], total: 1, next_cursor: null }),
          "GET /api/v1/families/7": json(aFamily({ version: 12 })),
          "POST /api/v1/families/7/members": json(aFamilyMember({ model_id: 9 })),
        },
      },
    );
    expect(screen.getByRole("button", { name: "Add to Family" })).toBeDisabled();
    await user.click(await screen.findByRole("radio", { name: /Benchy variations/ }));
    await user.selectOptions(screen.getByLabelText("Variation"), "repaired");
    await user.click(screen.getByRole("button", { name: "Add to Family" }));
    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      model_id: 9,
      role: "repaired",
      version: 12,
    });
    expect(requestsWithMethod("DELETE")).toHaveLength(0);
  });

  it("preserves the selected Family across searches", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <JoinFamilyDialog modelId={9} onClose={() => {}} onJoined={() => {}} />,
      {
        routes: {
          "GET /api/v1/families": (url) =>
            json({
              items: url.includes("q=tools") ? [aFamily({ id: 8, name: "Tools" })] : [aFamily()],
              total: 1,
              next_cursor: null,
            }),
          "GET /api/v1/families/7": json(aFamily()),
          "POST /api/v1/families/7/members": json(aFamilyMember({ model_id: 9 })),
        },
      },
    );
    await user.click(await screen.findByRole("radio", { name: /Benchy variations/ }));
    await user.type(screen.getByRole("textbox", { name: "Search Families…" }), "tools");
    expect(await screen.findByRole("radio", { name: /Tools/ })).not.toBeChecked();
    expect(screen.getByText("Selected: Benchy variations")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Add to Family" }));
    await waitFor(() =>
      expect(requestsWithMethod("POST")[0]?.url).toBe("/api/v1/families/7/members"),
    );
  });

  it("retains selection while loading another Family page", async () => {
    const user = userEvent.setup();
    renderApp(<JoinFamilyDialog modelId={9} onClose={() => {}} onJoined={() => {}} />, {
      routes: {
        "GET /api/v1/families": (url) =>
          json(
            url.includes("cursor=next")
              ? { items: [aFamily({ id: 8, name: "Tools" })], total: 2, next_cursor: null }
              : { items: [aFamily()], total: 2, next_cursor: "next" },
          ),
      },
    });
    await user.click(await screen.findByRole("radio", { name: /Benchy variations/ }));
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByRole("radio", { name: /Tools/ })).toBeEnabled();
    expect(screen.getByRole("radio", { name: /Benchy variations/ })).toBeChecked();
  });

  it("retains the join draft after a membership conflict", async () => {
    const user = userEvent.setup();
    renderApp(<JoinFamilyDialog modelId={9} onClose={() => {}} onJoined={() => {}} />, {
      routes: {
        "GET /api/v1/families": json({ items: [aFamily()], total: 1, next_cursor: null }),
        "GET /api/v1/families/7": json(aFamily()),
        "POST /api/v1/families/7/members": json({ detail: "family_membership_conflict" }, 409),
      },
    });
    await user.click(await screen.findByRole("radio", { name: /Benchy variations/ }));
    await user.selectOptions(screen.getByLabelText("Variation"), "rescaled");
    await user.click(screen.getByRole("button", { name: "Add to Family" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("explicit move");
    expect(screen.getByRole("radio", { name: /Benchy variations/ })).toBeChecked();
    expect(screen.getByLabelText("Variation")).toHaveValue("rescaled");
  });

  it("rechecks edit authority before joining", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <JoinFamilyDialog modelId={9} onClose={() => {}} onJoined={() => {}} />,
      {
        routes: {
          "GET /api/v1/families": json({
            items: [aFamily(), aFamily({ id: 8, name: "Shared tools", effective_role: "view" })],
            total: 2,
            next_cursor: null,
          }),
          "GET /api/v1/families/7": json(aFamily({ effective_role: "view" })),
        },
      },
    );
    expect(await screen.findByRole("radio", { name: /Shared tools/ })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: /Benchy variations/ }));
    await user.click(screen.getByRole("button", { name: "Add to Family" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Editing requires edit access");
    expect(requestsWithMethod("POST")).toHaveLength(0);
  });

  it("retries a failed Family search", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderApp(<JoinFamilyDialog modelId={9} onClose={() => {}} onJoined={() => {}} />, {
      routes: {
        "GET /api/v1/families": () =>
          failed
            ? json({ detail: "failure" }, 503)
            : json({ items: [], total: 0, next_cursor: null }),
      },
    });
    expect(await screen.findByRole("alert")).toBeVisible();
    failed = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No Families match this search.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Add to Family" })).toBeDisabled();
  });
});
