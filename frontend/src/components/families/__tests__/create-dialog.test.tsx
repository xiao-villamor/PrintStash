/** Creation is an explicit grouping decision; paging cannot discard that decision. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreateFamilyDialog } from "../create-dialog";
import { aModelListItem } from "@/test-support/factories";
import { aFamily, aFamilySummary } from "@/test-support/families";
import { json, memberSession, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());

describe("Create Family", () => {
  it("suggests the chosen canonical name without replacing a custom name", async () => {
    const user = userEvent.setup();
    renderApp(
      <CreateFamilyDialog
        models={[aModelListItem({ name: "Benchy" }), aModelListItem({ id: 2, name: "Spatula" })]}
        onClose={() => {}}
        onCreated={() => {}}
      />,
    );
    await user.click(screen.getByRole("radio", { name: "Benchy" }));
    expect(screen.getByLabelText("Family name")).toHaveValue("Benchy");
    await user.clear(screen.getByLabelText("Family name"));
    await user.type(screen.getByLabelText("Family name"), "Workshop tools");
    await user.click(screen.getByRole("radio", { name: "Spatula" }));
    expect(screen.getByLabelText("Family name")).toHaveValue("Workshop tools");
  });
  it("requires an explicit canonical selection", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn<(family: ReturnType<typeof aFamily>) => void>();
    const model = aModelListItem({ name: "Benchy" });
    const { requestsWithMethod } = renderApp(
      <CreateFamilyDialog models={[model]} onClose={() => {}} onCreated={onCreated} />,
      {
        routes: {
          "GET /api/v1/models/page": json({ items: [model], total: 1, next_cursor: null }),
          "POST /api/v1/families": json(aFamily(), 201),
        },
      },
    );
    await user.type(screen.getByLabelText("Family name"), "Benchy variations");
    expect(screen.getByRole("button", { name: "Create Family" })).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: "Benchy" }));
    await user.click(screen.getByRole("button", { name: "Create Family" }));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(aFamily()));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      name: "Benchy variations",
      canonical_model_id: 1,
      members: [{ model_id: 1, role: "identical", sort_order: 0 }],
    });
  });

  it("preserves selected Models across member pages", async () => {
    const user = userEvent.setup();
    const first = aModelListItem({ id: 1, name: "Benchy" });
    const second = aModelListItem({ id: 2, name: "Spatula" });
    renderApp(<CreateFamilyDialog onClose={() => {}} onCreated={() => {}} />, {
      routes: {
        "GET /api/v1/models/page": (url) =>
          json(
            url.includes("cursor=next")
              ? { items: [second], total: 2, next_cursor: null }
              : { items: [first], total: 2, next_cursor: "next" },
          ),
      },
    });
    await user.click(await screen.findByRole("checkbox", { name: "Select Benchy" }));
    await user.click(screen.getByRole("button", { name: "Load more" }));
    await user.click(await screen.findByRole("checkbox", { name: "Select Spatula" }));
    expect(screen.getByRole("checkbox", { name: "Select Benchy" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Benchy" })).toBeVisible();
    expect(screen.getByRole("radio", { name: "Spatula" })).toBeVisible();
  });

  it("preserves selection across search changes", async () => {
    const user = userEvent.setup();
    const first = aModelListItem({ id: 1, name: "Benchy" });
    const second = aModelListItem({ id: 2, name: "Spatula" });
    const { requestsWithMethod } = renderApp(
      <CreateFamilyDialog onClose={() => {}} onCreated={() => {}} />,
      {
        routes: {
          "GET /api/v1/models/page": (url) =>
            json({
              items: url.includes("q=spatula") ? [second] : [first],
              total: 1,
              next_cursor: null,
            }),
          "POST /api/v1/families": json(aFamily(), 201),
        },
      },
    );
    await user.click(await screen.findByRole("checkbox", { name: "Select Benchy" }));
    await user.type(screen.getByRole("textbox", { name: "Add Models" }), "spatula");
    await user.click(await screen.findByRole("checkbox", { name: "Select Spatula" }));
    expect(screen.getByRole("radio", { name: "Benchy" })).toBeVisible();
    await user.click(screen.getByRole("radio", { name: "Spatula" }));
    await user.type(screen.getByLabelText("Family name"), "Tool variants");
    await user.click(screen.getByRole("button", { name: "Create Family" }));
    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toMatchObject({
      canonical_model_id: 2,
      members: [
        { model_id: 1, role: "identical", sort_order: 0 },
        { model_id: 2, role: "identical", sort_order: 1 },
      ],
    });
  });

  it("refuses already grouped Models during creation", async () => {
    const model = aModelListItem({ family: aFamilySummary() });
    const { requestsWithMethod } = renderApp(
      <CreateFamilyDialog models={[model]} onClose={() => {}} onCreated={() => {}} />,
      {
        routes: {
          "GET /api/v1/models/page": json({ items: [model], total: 1, next_cursor: null }),
        },
      },
    );
    expect(screen.getByRole("alert")).toHaveTextContent("already belongs to a Family");
    expect(screen.getByRole("button", { name: "Create Family" })).toBeDisabled();
    expect(requestsWithMethod("POST")).toHaveLength(0);
  });

  it("disables Models without edit permission", async () => {
    const model = aModelListItem({ name: "Read-only boat", effective_role: "view" });
    renderApp(<CreateFamilyDialog onClose={() => {}} onCreated={() => {}} />, {
      auth: memberSession(),
      routes: { "GET /api/v1/models/page": json({ items: [model], total: 1, next_cursor: null }) },
    });
    expect(await screen.findByRole("checkbox", { name: "Select Read-only boat" })).toBeDisabled();
  });

  it("retains the create draft after conflict", async () => {
    const user = userEvent.setup();
    const model = aModelListItem({ name: "Benchy" });
    renderApp(<CreateFamilyDialog models={[model]} onClose={() => {}} onCreated={() => {}} />, {
      routes: {
        "GET /api/v1/models/page": json({ items: [], total: 0, next_cursor: null }),
        "POST /api/v1/families": json({ detail: "family_membership_conflict" }, 409),
      },
    });
    await user.type(screen.getByLabelText("Family name"), "My variations");
    await user.click(screen.getByRole("radio", { name: "Benchy" }));
    await user.click(screen.getByRole("button", { name: "Create Family" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("explicit move");
    expect(screen.getByLabelText("Family name")).toHaveValue("My variations");
    expect(screen.getByRole("radio", { name: "Benchy" })).toBeChecked();
  });
});
