/** Membership changes keep human intent explicit through versioned transactions. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AddFamilyMemberDialog,
  CanonicalFamilyDialog,
  EditFamilyMemberDialog,
} from "../member-dialogs";
import { aModelListItem } from "@/test-support/factories";
import { aFamily, aFamilyMember, aFamilySummary } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());

describe("Family membership dialogs", () => {
  it("reviews a cross-Family move before one atomic write", async () => {
    const user = userEvent.setup();
    const model = aModelListItem({
      id: 9,
      name: "Large Benchy",
      family: aFamilySummary({ id: 6, version: 1, name: "Old boats", canonical_model_id: 9 }),
    });
    const { requestsWithMethod } = renderApp(
      <AddFamilyMemberDialog family={aFamily()} onClose={() => {}} onSaved={() => {}} />,
      {
        routes: {
          "GET /api/v1/models/page": json({ items: [model], total: 1, next_cursor: null }),
          "GET /api/v1/families/6": json(
            aFamily({ id: 6, name: "Old boats", version: 12, canonical_model_id: 9 }),
          ),
          "GET /api/v1/families/7": json(aFamily({ version: 5 })),
          "POST /api/v1/families/7/move-member": json(aFamilyMember()),
        },
      },
    );
    await user.click(await screen.findByRole("checkbox", { name: "Select Large Benchy" }));
    await user.selectOptions(screen.getByLabelText("Variation of Large Benchy"), "rescaled");
    await user.click(screen.getByRole("button", { name: "Review move" }));
    const review = await screen.findByRole("dialog", { name: "Review move" });
    expect(within(review).getByText(/leaves its canonical position vacant/)).toBeVisible();
    expect(requestsWithMethod("POST")).toHaveLength(0);
    expect(requestsWithMethod("DELETE")).toHaveLength(0);
    await user.click(within(review).getByRole("button", { name: "Move to this Family" }));
    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(requestsWithMethod("POST")[0].url).toBe("/api/v1/families/7/move-member");
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      model_id: 9,
      role: "rescaled",
      source_family_id: 6,
      source_version: 12,
      destination_version: 5,
    });
  });

  it("rechecks permissions when preparing a move", async () => {
    const user = userEvent.setup();
    const model = aModelListItem({ name: "Benchy", family: aFamilySummary({ id: 6 }) });
    renderApp(<AddFamilyMemberDialog family={aFamily()} onClose={() => {}} onSaved={() => {}} />, {
      routes: {
        "GET /api/v1/models/page": json({ items: [model], total: 1, next_cursor: null }),
        "GET /api/v1/families/6": json(aFamily({ id: 6, effective_role: "view" })),
        "GET /api/v1/families/7": json(aFamily()),
      },
    });
    await user.click(await screen.findByRole("checkbox", { name: "Select Benchy" }));
    await user.click(screen.getByRole("button", { name: "Review move" }));
    expect(await screen.findByRole("button", { name: "Move to this Family" })).toBeDisabled();
    expect(screen.getByText(/Editing requires edit access/)).toBeVisible();
  });

  it("adds an ungrouped Model with its chosen role", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <AddFamilyMemberDialog family={aFamily()} onClose={() => {}} onSaved={() => {}} />,
      {
        routes: {
          "GET /api/v1/models/page": json({
            items: [aModelListItem({ id: 4, name: "Repaired boat" })],
            total: 1,
            next_cursor: null,
          }),
          "POST /api/v1/families/7/members": json(aFamilyMember()),
        },
      },
    );
    await user.click(await screen.findByRole("checkbox", { name: "Select Repaired boat" }));
    await user.selectOptions(screen.getByLabelText("Variation of Repaired boat"), "repaired");
    await user.click(screen.getByRole("button", { name: "Add member" }));
    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      model_id: 4,
      role: "repaired",
      version: 3,
    });
  });

  it("clears an unknown scale without altering files", async () => {
    const user = userEvent.setup();
    const member = aFamilyMember({ role: "rescaled", scale_factor: 2 });
    const { requestsWithMethod } = renderApp(
      <EditFamilyMemberDialog
        family={aFamily()}
        member={member}
        onClose={() => {}}
        onSaved={() => {}}
      />,
      { routes: { "PATCH /api/v1/families/7/members/11": json(member) } },
    );
    await user.clear(screen.getByLabelText("Scale"));
    await user.type(screen.getByLabelText("Transformation note"), "Scale not verified");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PATCH")[0].body)).toMatchObject({
      version: 3,
      scale_factor: null,
      transformation_note: "Scale not verified",
      role: "rescaled",
    });
    expect(requestsWithMethod("DELETE")).toHaveLength(0);
  });

  it("keeps canonical relative controls unavailable", () => {
    renderApp(
      <EditFamilyMemberDialog
        family={aFamily()}
        member={aFamilyMember()}
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );
    expect(screen.queryByLabelText("Scale")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Transformation note")).toBeVisible();
  });

  it("changes canonical with the human-selected previous role", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <CanonicalFamilyDialog
        family={aFamily()}
        member={aFamilyMember({ id: 12, model_id: 2, role: "repaired" })}
        onClose={() => {}}
        onSaved={() => {}}
      />,
      {
        routes: {
          "POST /api/v1/families/7/canonical": json(
            aFamily({ canonical_member_id: 12, canonical_model_id: 2 }),
          ),
        },
      },
    );
    await user.selectOptions(screen.getByLabelText("Previous canonical becomes"), "print_variant");
    await user.click(screen.getByRole("button", { name: "Make canonical" }));
    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      member_id: 12,
      previous_role: "print_variant",
      version: 3,
    });
  });
});
