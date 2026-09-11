import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyBulkDialog, FamilyMetadataDialog } from "../metadata-dialog";
import { aModel, aModelListItem } from "@/test-support/factories";
import { aFamily, aFamilyMember } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());
const close = () => {};
const base = { "GET /api/v1/collections": json([]) };

describe("Family metadata", () => {
  it("updates Family metadata without changing member Models", async () => {
    const user = userEvent.setup();
    const saved = vi.fn<() => void>();
    const { requestsWithMethod } = renderApp(
      <FamilyMetadataDialog
        family={aFamily({ description: "Old note" })}
        onSaved={saved}
        onClose={close}
      />,
      {
        routes: { ...base, "PATCH /api/v1/families/7": json(aFamily()) },
      },
    );
    await user.clear(screen.getByRole("textbox", { name: "Family name" }));
    await user.type(screen.getByRole("textbox", { name: "Family name" }), "Workshop variations");
    await user.clear(screen.getByRole("textbox", { name: "Description" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saved).toHaveBeenCalledOnce());
    expect(requestsWithMethod("PATCH")).toHaveLength(1);
    expect(JSON.parse(requestsWithMethod("PATCH")[0].body)).toEqual({
      version: 3,
      name: "Workshop variations",
      description: null,
      collection_id: null,
      cover_model_id: null,
      cover_image_url: null,
    });
  });

  it("selects a cover beyond the first page without losing the selection on search", async () => {
    const user = userEvent.setup();
    const member = aFamilyMember({
      id: 13,
      model_id: 3,
      model: aModelListItem({ id: 3, name: "Large Spatula" }),
    });
    const { requestsWithMethod } = renderApp(
      <FamilyMetadataDialog family={aFamily()} onSaved={close} onClose={close} />,
      {
        routes: {
          ...base,
          "GET /api/v1/families/7/members": (url) =>
            json(
              url.includes("q=missing")
                ? { items: [], total: 0, next_cursor: null }
                : url.includes("cursor=more")
                  ? { items: [member], total: 2, next_cursor: null }
                  : { items: [aFamilyMember()], total: 2, next_cursor: "more" },
            ),
          "GET /api/v1/models/3": json(aModel({ id: 3, name: "Large Spatula" })),
          "PATCH /api/v1/families/7": json(aFamily()),
        },
      },
    );
    await user.click(screen.getByText("Cover", { selector: "summary" }));
    await user.click(await screen.findByRole("button", { name: "Load more" }));
    await screen.findByRole("option", { name: "Large Spatula" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Cover Model" }), "3");
    await user.type(screen.getByRole("textbox", { name: "Search your library…" }), "missing");
    await waitFor(() =>
      expect(requestsWithMethod("GET").some((request) => request.url.includes("q=missing"))).toBe(
        true,
      ),
    );
    expect(screen.getByRole("combobox", { name: "Cover Model" })).toHaveValue("3");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PATCH")[0].body)).toMatchObject({
      cover_model_id: 3,
      cover_image_url: null,
    });
  });

  it("uses the upload response version when subsequently saving metadata", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <FamilyMetadataDialog family={aFamily()} onSaved={close} onClose={close} />,
      {
        routes: {
          ...base,
          "GET /api/v1/families/7/members": json({ items: [], total: 0 }),
          "PUT /api/v1/families/7/cover": json(aFamily({ version: 4, cover_image_uploaded: true })),
          "PATCH /api/v1/families/7": json(aFamily({ version: 5 })),
        },
      },
    );
    await user.click(screen.getByText("Cover", { selector: "summary" }));
    await user.selectOptions(screen.getByRole("combobox", { name: "Cover" }), "upload");
    await user.upload(
      screen.getByLabelText("Upload image", { selector: "input" }),
      new File(["image"], "cover.png", { type: "image/png" }),
    );
    await screen.findByRole("button", { name: "Remove uploaded cover" });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PATCH")[0].body)).toEqual({
      version: 4,
      name: "Benchy variations",
      description: null,
      collection_id: null,
    });
  });

  it("allows saving after removing an uploaded cover", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <FamilyMetadataDialog
        family={aFamily({ cover_image_uploaded: true })}
        onSaved={close}
        onClose={close}
      />,
      {
        routes: {
          ...base,
          "GET /api/v1/families/7/members": json({ items: [], total: 0 }),
          "DELETE /api/v1/families/7/cover": json(aFamily({ version: 4 })),
          "PATCH /api/v1/families/7": json(aFamily({ version: 5 })),
        },
      },
    );
    await user.click(screen.getByText("Cover", { selector: "summary" }));
    await user.click(screen.getByRole("button", { name: "Remove uploaded cover" }));
    expect(await screen.findByRole("combobox", { name: "Cover Model" })).toHaveValue("");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PATCH")[0].body)).toMatchObject({
      version: 4,
      cover_model_id: null,
    });
  });

  it.each(["en", "es"] as const)(
    "retains and explains the metadata conflict in %s",
    async (locale) => {
      const user = userEvent.setup();
      renderApp(<FamilyMetadataDialog family={aFamily()} onSaved={close} onClose={close} />, {
        locale,
        routes: {
          ...base,
          "PATCH /api/v1/families/7": json({ detail: "family_revision_conflict" }, 409),
        },
      });
      const description = locale === "en" ? "Description" : "Descripción";
      await user.type(screen.getByRole("textbox", { name: description }), "Keep this note");
      await user.click(screen.getByRole("button", { name: locale === "en" ? "Save" : "Guardar" }));
      expect(await screen.findByRole("alert")).toHaveTextContent(
        locale === "en" ? "changed elsewhere" : "ha cambiado",
      );
      expect(screen.getByRole("textbox", { name: description })).toHaveValue("Keep this note");
    },
  );

  it("applies deduplicated tags to live member Models through the explicit bulk operation", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(
      <FamilyBulkDialog family={aFamily()} mode="tags" onSaved={close} onClose={close} />,
      {
        routes: { ...base, "POST /api/v1/families/7/members/tags": json({ updated: 2 }) },
      },
    );
    await user.type(screen.getByRole("textbox", { name: "Tags" }), "tool, boat, tool");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toEqual({
      version: 3,
      add: ["tool", "boat"],
      remove: [],
    });
  });
});
