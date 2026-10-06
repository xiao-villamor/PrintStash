/** Multipart browser/editor behaviour for fixed parts, alternatives, and unavailable Models. */
import "@testing-library/jest-dom/vitest";

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MultipartModelDetailPage } from "@/components/multipart-model-browser";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { aCollectionNode } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { CollectionRead, MultipartModelRead } from "@/types";

function aMultipart(over: Partial<MultipartModelRead> = {}): MultipartModelRead {
  return {
    id: 7,
    name: "Desk organiser",
    slug: "desk-organiser",
    description: null,
    collection: null,
    collection_id: null,
    collection_label: null,
    part_count: 0,
    model_count: 0,
    guide_count: 0,
    cover_model_id: null,
    cover_image_url: null,
    cover_image_uploaded: false,
    cover_thumbnail_url: null,
    starred: false,
    member_model_ids: [],
    tags: [],
    effective_role: "admin",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    parts: [],
    guides: [],
    ...over,
  };
}

const model = {
  id: 12,
  choice_id: 101,
  name: "Desk base",
  slug: "desk-base",
  thumbnail_url: null,
  source_file_count: 1,
  gcode_revision_count: 2,
  available: true,
};

const alternative = {
  id: 13,
  choice_id: 102,
  name: "Desk base compact",
  slug: "desk-base-compact",
  thumbnail_url: null,
  source_file_count: 2,
  gcode_revision_count: 1,
  available: true,
};

const collection: CollectionRead = {
  id: 3,
  name: "Parts",
  slug: "parts",
  path: "parts",
  parent_id: null,
  model_count: 4,
  effective_role: "admin",
  tags: [],
  has_readme: false,
};

describe("MultipartModelDetailPage", () => {
  it("offers the first part action from the empty overview", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(aMultipart()),
        "GET /api/v1/multipart-models/7/candidates": json([model]),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));

    expect(await screen.findByRole("list", { name: "Choose existing models" })).toBeVisible();
  });

  it("shows an explicit empty description", async () => {
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    expect(await screen.findByText("No description yet")).toBeVisible();
  });

  it("shows vault only when no collection is assigned", async () => {
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    expect(await screen.findByText("Vault only")).toBeVisible();
  });

  it("returns to the exact unified-library view that opened the set", async () => {
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7?return=%2F%3Ftype%3Dall%26tag%3Dfantasy",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    expect(await screen.findByRole("link", { name: "Multipart sets" })).toHaveAttribute(
      "href",
      "/?type=all&tag=fantasy",
    );
  });

  it("opens as a visual overview", async () => {
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(
          aMultipart({
            description: "Everything needed for the organiser",
            part_count: 1,
            model_count: 1,
            parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }],
          }),
        ),
      },
    });

    expect(await screen.findByText("Everything needed for the organiser")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Base" })).toBeVisible();
    expect(screen.getByRole("link", { name: /Desk base/ })).toHaveAttribute("href", "/models/12");
    expect(screen.getByRole("button", { name: "Edit multipart set" })).toBeVisible();
    expect(screen.queryByLabelText("Part name")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();
  });

  it("reveals management controls after entering edit mode", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(
          aMultipart({
            part_count: 1,
            model_count: 1,
            parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }],
          }),
        ),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));

    expect(screen.getByLabelText("Part name")).toHaveValue("Base");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeVisible();
  });

  it("keeps metadata actions inside edit mode", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    await screen.findByRole("heading", { name: "Desk organiser" });
    expect(screen.queryByRole("button", { name: "Edit description" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Change collection" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit multipart set" }));

    expect(screen.getByRole("textbox", { name: "Description" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Collection" })).toBeVisible();
  });

  it("edits tags only from the multipart edit page", async () => {
    const user = userEvent.setup();
    const saved = aMultipart({ tags: ["Fantasy", "Display"] });
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(aMultipart({ tags: ["Fantasy"] })),
        "GET /api/v1/tags": json([
          { id: 1, name: "Fantasy", slug: "fantasy", model_count: 0 },
          { id: 2, name: "Display", slug: "display", model_count: 0 },
        ]),
        "PUT /api/v1/multipart-models/7/tags": json(saved),
      },
    });

    expect(await screen.findByText("Fantasy")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Edit tags" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Edit tags" }));
    await user.click(await screen.findByRole("button", { name: "Display" }));
    await user.click(screen.getByRole("button", { name: "Save tags" }));

    await waitFor(() =>
      expect(
        requestsWithMethod("PUT").find((request) => request.url.endsWith("/tags")),
      ).toBeDefined(),
    );
    const request = requestsWithMethod("PUT").find((item) => item.url.endsWith("/tags"));
    expect(JSON.parse(request?.body ?? "{}")).toEqual({ tags: ["Fantasy", "Display"] });
  });

  it("discards an unsaved draft when editing is cancelled", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    const name = screen.getByRole("textbox", { name: "Name" });
    await user.clear(name);
    await user.type(name, "Unsaved name");
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("heading", { name: "Desk organiser" })).toBeVisible();
    expect(screen.queryByDisplayValue("Unsaved name")).not.toBeInTheDocument();
  });

  it("persists the reordered pieces", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 2,
      model_count: 2,
      parts: [
        { id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] },
        { id: 2, name: "Lid", quantity: 1, sort_order: 1, models: [alternative] },
      ],
    });
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(detail),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await screen.findByDisplayValue("Lid");
    await user.click(screen.getByRole("button", { name: "Move piece up: Lid" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
    expect(
      JSON.parse(requestsWithMethod("PUT")[0].body).parts.map(
        (part: { name: string }) => part.name,
      ),
    ).toEqual(["Lid", "Base"]);
  });

  it("uploads a guide into the multipart set", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(aMultipart()),
        "POST /api/v1/documents/upload": json({
          id: 44,
          edit_version: 1,
          name: "Assembly",
          kind: "pdf",
          collection: null,
          collection_id: null,
          multipart_model_id: 7,
          filename: "assembly.pdf",
          effective_role: "admin",
          updated_at: "2026-01-02T00:00:00Z",
          body: null,
        }),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await screen.findByText("No guides yet");
    const input = document.querySelector<HTMLInputElement>('input[type="file"][accept^=".pdf"]');
    expect(input).not.toBeNull();
    await user.upload(input!, new File(["pdf"], "assembly.pdf", { type: "application/pdf" }));

    expect(await screen.findByRole("link", { name: "Assembly" })).toHaveAttribute(
      "href",
      "/documents/44",
    );
  });

  it("removes a guide from the set", async () => {
    const user = userEvent.setup();
    const guide = {
      id: 44,
      edit_version: 1,
      name: "Assembly",
      kind: "pdf" as const,
      collection: null,
      collection_id: null,
      multipart_model_id: 7,
      filename: "assembly.pdf",
      effective_role: "admin" as const,
      updated_at: "2026-01-02T00:00:00Z",
    };
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(aMultipart({ guide_count: 1, guides: [guide] })),
        "DELETE /api/v1/documents/44": new Response(null, { status: 204 }),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Remove guide: Assembly" }));

    await waitFor(() => expect(requestsWithMethod("DELETE")).toHaveLength(1));
    expect(screen.queryByRole("link", { name: "Assembly" })).not.toBeInTheDocument();
  });

  it("shows an empty editor with a first-part action", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    expect(
      (await screen.findAllByRole("button", { name: /Add (the first part|a part)/i }))[0],
    ).toBeVisible();
  });

  it("removes the whole part when its last Model is removed", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(
          aMultipart({
            part_count: 1,
            model_count: 1,
            parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }],
          }),
        ),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    expect(screen.getByLabelText("Part name")).toBeVisible();

    await user.click(screen.getByRole("button", { name: /Remove model.*Desk base/ }));
    expect(screen.queryByLabelText("Part name")).not.toBeInTheDocument();
    expect(
      screen.getAllByRole("button", { name: /Add (the first part|a part)/i })[0],
    ).toBeVisible();
  });

  it("adds a fixed part from the empty editor", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(aMultipart()),
        "GET /api/v1/multipart-models/7/candidates": json([model]),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await screen.findByText("No pieces added yet");
    await user.click(screen.getAllByRole("button", { name: /Add (the first part|a part)/i })[0]);
    await user.click(await screen.findByRole("button", { name: /Desk base/ }));
    await user.click(screen.getByRole("button", { name: "Add parts (1)" }));

    expect(screen.getByDisplayValue("Part 1")).toBeVisible();
    expect(screen.getAllByText("Desk base")[0]).toBeVisible();
  });

  it("reveals alternatives after selecting a second Model", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(
          aMultipart({
            part_count: 1,
            model_count: 1,
            parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }],
          }),
        ),
        "GET /api/v1/multipart-models/7/candidates": json([model, alternative]),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await screen.findByDisplayValue("Base");
    await user.click(screen.getByRole("button", { name: "Add variant" }));
    const options = await screen.findAllByRole("listitem");
    expect(options[0].querySelector("button")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: /Desk base compact/ }));
    await user.click(screen.getByRole("button", { name: "Add variants (1)" }));

    expect(screen.getByText(/Choose one/)).toBeVisible();
    expect(screen.getAllByText("Desk base compact")[0]).toBeVisible();
  });

  it("saves the edited part payload", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 1,
      model_count: 1,
      parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }],
    });
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(detail),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    const partName = screen.getByDisplayValue("Base");
    await user.clear(partName);
    await user.type(partName, "Top");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText("Changes saved")).toBeVisible();
    expect(JSON.parse(requestsWithMethod("PUT")[0].body)).toEqual({
      name: "Desk organiser",
      description: null,
      collection_id: null,
      cover_model_id: null,
      cover_image_url: null,
      parts: [{ name: "Top", quantity: 1, choices: [{ model_id: 12, choice_id: 101 }] }],
    });
  });

  it("saves an edited description", async () => {
    const user = userEvent.setup();
    const detail = aMultipart();
    const saved = { ...detail, description: "Print the base before the clips." };
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(saved),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await user.type(
      screen.getByRole("textbox", { name: "Description" }),
      "Print the base before the clips.",
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(JSON.parse(requestsWithMethod("PUT")[0].body).description).toBe(
      "Print the base before the clips.",
    );
    expect(await screen.findByText("Print the base before the clips.")).toBeVisible();
  });

  it("saves the selected collection", async () => {
    const user = userEvent.setup();
    const detail = aMultipart();
    const saved = {
      ...detail,
      collection: collection.path,
      collection_id: collection.id,
      collection_label: "Parts",
    };
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        ...collectionTreeRoutes([collection]),
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(saved),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Collection" }));
    await user.click(await screen.findByRole("option", { name: /Parts/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(JSON.parse(requestsWithMethod("PUT")[0].body).collection_id).toBe(3);
    expect(await screen.findByText("Parts")).toBeVisible();
  });

  it("saves an external image as the set cover", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 1,
      model_count: 2,
      parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model, alternative] }],
    });
    const coverImageUrl = "https://images.example.test/desk-organiser.webp";
    const saved = {
      ...detail,
      cover_image_url: coverImageUrl,
      cover_thumbnail_url: coverImageUrl,
    };
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(saved),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("textbox", { name: "Or use an image URL" }));
    await user.paste(coverImageUrl);
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PUT")[0].body).cover_image_url).toBe(coverImageUrl);
    expect(screen.getByText("Set cover")).toBeVisible();
  });

  it("uploads a cover image from the computer without requiring a URL", async () => {
    const user = userEvent.setup();
    const detail = aMultipart();
    const uploaded = {
      ...detail,
      cover_image_uploaded: true,
      cover_thumbnail_url: "/api/v1/multipart-models/7/cover/content?v=cover.webp",
    };
    const { container, requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7/cover": json(uploaded),
        "GET /api/v1/multipart-models/7/cover/content": new Response("cover", {
          headers: { "content-type": "image/webp" },
        }),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    const input = container.querySelector<HTMLInputElement>(
      'input[type="file"][accept="image/png,image/jpeg,image/webp"]',
    );
    expect(input).not.toBeNull();
    await user.upload(input!, new File(["cover"], "figure.png", { type: "image/png" }));

    await waitFor(() => {
      expect(requestsWithMethod("PUT").some((request) => request.url.endsWith("/cover"))).toBe(
        true,
      );
    });
    expect(await screen.findByText("Uploaded from your computer")).toBeVisible();
    expect(screen.getByRole("button", { name: "Remove uploaded cover" })).toBeVisible();
  });

  it("allows cancelling the delete confirmation", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: { "GET /api/v1/multipart-models/7": json(aMultipart()) },
    });

    await screen.findByRole("heading", { name: "Desk organiser" });
    await user.click(screen.getByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Delete multipart set" }));
    expect(screen.getByText(/Models, files and revisions stay in your library/)).toBeVisible();
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));

    await waitFor(() => {
      expect(
        screen.queryByText(/Models, files and revisions stay in your library/),
      ).not.toBeInTheDocument();
    });
  });

  it("keeps an unavailable member visible without linking it", async () => {
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(
          aMultipart({
            part_count: 1,
            model_count: 1,
            parts: [
              {
                id: 1,
                name: "Base",
                quantity: 1,
                sort_order: 0,
                models: [
                  {
                    id: 88,
                    choice_id: 901,
                    name: null,
                    slug: null,
                    thumbnail_url: null,
                    source_file_count: 0,
                    gcode_revision_count: 0,
                    available: false,
                  },
                ],
              },
            ],
          }),
        ),
      },
    });

    expect((await screen.findAllByText("Model unavailable"))[0]).toBeVisible();
    expect(screen.queryByRole("link", { name: "Model unavailable" })).not.toBeInTheDocument();
  });

  it("localizes a missing detail error in Spanish", async () => {
    renderApp(<MultipartModelDetailPage />, {
      locale: "es",
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json({ detail: "collection_not_found" }, 404),
      },
    });

    expect(
      await screen.findByText("Este modelo multiparte o colección ya no existe."),
    ).toBeVisible();
    expect(screen.queryByText("collection_not_found")).not.toBeInTheDocument();
  });

  it("keeps the local draft when saving parts fails", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 1,
      model_count: 1,
      parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }],
    });
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json({ detail: "save_failed" }, 500),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    const name = screen.getByDisplayValue("Desk organiser");
    await user.clear(name);
    await user.type(name, "Updated organiser");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't save changes. Check your access and try again.",
    );
    expect(screen.getByDisplayValue("Updated organiser")).toBeVisible();
  });

  it("round-trips then explicitly removes a redacted choice", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 1,
      model_count: 1,
      parts: [
        {
          id: 1,
          name: "Base",
          quantity: 1,
          sort_order: 0,
          models: [
            {
              id: 88,
              choice_id: 901,
              name: null,
              slug: null,
              thumbnail_url: null,
              source_file_count: 0,
              gcode_revision_count: 0,
              available: false,
            },
          ],
        },
      ],
    });
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(detail),
      },
    });

    expect((await screen.findAllByText("Model unavailable"))[0]).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: /Remove model.*Model unavailable/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PUT")[0].body)).toEqual({
      name: "Desk organiser",
      description: null,
      collection_id: null,
      cover_model_id: null,
      cover_image_url: null,
      parts: [],
    });
  });

  it("selects a model from the picker with the keyboard", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(aMultipart()),
        "GET /api/v1/multipart-models/7/candidates": json([model]),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await screen.findByText("No pieces added yet");
    await user.click(screen.getAllByRole("button", { name: /Add (the first part|a part)/i })[0]);
    const picker = await screen.findByRole("list", { name: "Choose existing models" });
    expect(picker).toBeVisible();
    const candidate = screen.getByRole("button", { name: /Desk base/ });
    candidate.focus();
    await user.keyboard("{Enter}");
    expect(candidate).toHaveAttribute("aria-pressed", "true");
    screen.getByRole("button", { name: "Add parts (1)" }).focus();
    await user.keyboard("{Enter}");

    expect(screen.getByDisplayValue("Part 1")).toBeVisible();
    expect(screen.getAllByText("Desk base")[0]).toBeVisible();
  });

  it("preserves distinct legacy alternatives including labels", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 1,
      model_count: 2,
      parts: [
        {
          id: 1,
          name: "Handle",
          quantity: 1,
          sort_order: 0,
          models: [
            {
              ...model,
              choice_id: 1001,
              legacy_label: "Short file",
              source_file_id: 501,
            },
            {
              ...model,
              choice_id: 1002,
              legacy_label: "Long file",
              source_file_id: 502,
            },
          ],
        },
      ],
    });
    const { requestsWithMethod } = renderApp(<MultipartModelDetailPage />, {
      at: "/multipart-models/7",
      routePath: "/multipart-models/:id",
      routes: {
        "GET /api/v1/multipart-models/7": json(detail),
        "PUT /api/v1/multipart-models/7": json(detail),
      },
    });

    expect(await screen.findByText("Short file")).toBeVisible();
    expect(screen.getByText("Long file")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PUT")[0].body).parts[0].choices).toEqual([
      { model_id: 12, choice_id: 1001 },
      { model_id: 12, choice_id: 1002 },
    ]);
  });
});

function renderPickerPage(
  routes: import("@/test-support/render").RouteTable = {},
  detail = aMultipart(),
  collections: CollectionRead[] = [],
) {
  return renderApp(<MultipartModelDetailPage />, {
    at: "/multipart-models/7",
    routePath: "/multipart-models/:id",
    routes: {
      "GET /api/v1/multipart-models/7": json(detail),
      "GET /api/v1/multipart-models/7/candidates": json([model, alternative]),
      ...collectionTreeRoutes(collections),
      ...routes,
    },
  });
}

describe("ModelPicker", () => {
  it("loads another folder page only when requested", async () => {
    const user = userEvent.setup();
    const { requests } = renderPickerPage({
      "GET /api/v1/collections/children": (url) =>
        json(
          new URL(url, "http://test").searchParams.has("cursor")
            ? {
                items: [aCollectionNode({ id: 4, name: "Bases", path: "bases" })],
                next_cursor: null,
              }
            : {
                items: [aCollectionNode({ id: 3, name: "Parts", path: "parts" })],
                next_cursor: "next",
              },
        ),
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    expect(await screen.findByRole("button", { name: "Parts" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Bases" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Show more folders" }));

    expect(await screen.findByRole("button", { name: "Bases" })).toBeVisible();
    expect(
      requests().filter((request) => request.url.includes("/collections/children")),
    ).toHaveLength(2);
  });

  it("adds multiple separate parts", async () => {
    const user = userEvent.setup();
    renderPickerPage();

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base$/ }));
    await user.click(screen.getByRole("button", { name: /^Desk base compact/ }));
    await user.click(screen.getByRole("button", { name: "Add parts (2)" }));

    expect(
      screen.getAllByLabelText("Part name").map((input) => input.getAttribute("value")),
    ).toEqual(["Part 1", "Part 2"]);
  });

  it("adds multiple variants to the same part", async () => {
    const user = userEvent.setup();
    renderPickerPage(
      {
        "GET /api/v1/multipart-models/7/candidates": json([
          alternative,
          { ...alternative, id: 14, name: "Wide base" },
        ]),
      },
      aMultipart({ parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }] }),
    );

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Add variant" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base compact/ }));
    await user.click(screen.getByRole("button", { name: /^Wide base/ }));
    await user.click(screen.getByRole("button", { name: "Add variants (2)" }));

    expect(screen.getAllByLabelText("Part name")).toHaveLength(1);
    expect(screen.getByRole("button", { name: /Remove model.*Wide base/ })).toBeVisible();
    expect(screen.getByRole("button", { name: /Remove model.*Desk base compact/ })).toBeVisible();
  });

  it("preserves selection across nested collections", async () => {
    const user = userEvent.setup();
    const { requests } = renderPickerPage(
      {
        "GET /api/v1/multipart-models/7/candidates": (url) =>
          json(
            new URL(url, "http://localhost").searchParams.get("collection") === "parts/bases"
              ? [alternative]
              : [model],
          ),
      },
      aMultipart(),
      [collection, { ...collection, id: 4, name: "Bases", path: "parts/bases", parent_id: 3 }],
    );

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base$/ }));
    await user.click(screen.getByRole("button", { name: "Parts" }));
    await user.click(await screen.findByRole("button", { name: "Bases" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base compact/ }));
    await user.click(screen.getByRole("button", { name: "Add parts (2)" }));

    expect(screen.getAllByLabelText("Part name")).toHaveLength(2);
    expect(
      requests().some((request) => request.url.includes("collection=parts%2Fbases&direct=true")),
    ).toBe(true);
  });

  it("returns to a parent collection through its breadcrumb", async () => {
    const user = userEvent.setup();
    renderPickerPage(
      {
        "GET /api/v1/multipart-models/7/candidates": (url) =>
          json(
            new URL(url, "http://localhost").searchParams.get("collection") === "parts/bases"
              ? [alternative]
              : [model],
          ),
      },
      aMultipart(),
      [
        collection,
        { ...collection, id: 4, name: "Bases", path: "parts/bases", parent_id: 3 },
        { ...collection, id: 5, name: "Mounts", path: "parts/mounts", parent_id: 3 },
      ],
    );

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base$/ }));
    await user.click(screen.getByRole("button", { name: "Parts" }));
    await user.click(await screen.findByRole("button", { name: "Bases" }));
    await screen.findByRole("button", { name: /^Desk base compact/ });
    const breadcrumb = screen.getByRole("navigation", { name: "Browse collections" });
    await user.click(within(breadcrumb).getByRole("button", { name: "Parts" }));

    expect(await screen.findByRole("button", { name: /^Desk base$/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Mounts" })).toBeVisible();
  });

  it("retains existing parts across successive selections", async () => {
    const user = userEvent.setup();
    renderPickerPage(
      {
        "GET /api/v1/multipart-models/7/candidates": json([
          alternative,
          { ...alternative, id: 14, name: "Wide base" },
          { ...alternative, id: 15, name: "Top" },
        ]),
      },
      aMultipart({ parts: [{ id: 1, name: "Base", quantity: 1, sort_order: 0, models: [model] }] }),
    );

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: "Add another part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base compact/ }));
    await user.click(screen.getByRole("button", { name: "Wide base" }));
    await user.click(screen.getByRole("button", { name: "Add parts (2)" }));
    await user.click(screen.getByRole("button", { name: "Add another part" }));
    await user.click(await screen.findByRole("button", { name: "Top" }));
    await user.click(screen.getByRole("button", { name: "Add parts (1)" }));

    expect(
      screen.getAllByLabelText("Part name").map((input) => input.getAttribute("value")),
    ).toEqual(["Base", "Part 2", "Part 3", "Part 4"]);
  });

  it("preserves selection across pages", async () => {
    const user = userEvent.setup();
    renderPickerPage({
      "GET /api/v1/multipart-models/7/candidates": (url) =>
        json(
          new URL(url, "http://localhost").searchParams.get("offset") === "48"
            ? [alternative]
            : Array.from({ length: 49 }, (_, index) => ({
                ...model,
                id: 100 + index,
                name: `Model ${index}`,
              })),
        ),
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Model 0$/ }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base compact/ }));
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByRole("button", { name: /^Model 0$/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Add parts (2)" }));

    expect(screen.getAllByLabelText("Part name")).toHaveLength(2);
    // This complete 48-card round trip across pages takes over five seconds with V8 coverage.
  }, 10_000);

  it("cancels selection without editing the composition", async () => {
    const user = userEvent.setup();
    renderPickerPage();

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base$/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByLabelText("Part name")).not.toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: /Add (the first part|a part)/i })[0]);

    expect(screen.getByRole("button", { name: "Add parts (0)" })).toBeDisabled();
  });

  it("deselects a previously selected model", async () => {
    const user = userEvent.setup();
    renderPickerPage();

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    const candidate = await screen.findByRole("button", { name: /^Desk base$/ });
    await user.click(candidate);
    await user.click(candidate);

    expect(candidate).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Add parts (0)" })).toBeDisabled();
  });

  it("disables unavailable models", async () => {
    const user = userEvent.setup();
    renderPickerPage({
      "GET /api/v1/multipart-models/7/candidates": json([
        { ...model, available: false, name: null },
      ]),
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));

    expect(await screen.findByRole("button", { name: /unavailable/i })).toBeDisabled();
  });

  it("retries a failed candidate request", async () => {
    const user = userEvent.setup();
    const { route } = renderPickerPage({
      "GET /api/v1/multipart-models/7/candidates": json({ detail: "offline" }, 500),
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load models. Try again.");
    route({ "GET /api/v1/multipart-models/7/candidates": json([model]) });
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("button", { name: /^Desk base$/ })).toBeVisible();
  });

  it("shows an empty candidate result", async () => {
    const user = userEvent.setup();
    renderPickerPage({ "GET /api/v1/multipart-models/7/candidates": json([]) });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));

    expect(await screen.findByText("No models match this search.")).toBeVisible();
  });

  it("preserves selection while searching", async () => {
    const user = userEvent.setup();
    renderPickerPage({
      "GET /api/v1/multipart-models/7/candidates": (url) =>
        json(new URL(url, "http://localhost").searchParams.get("q") ? [alternative] : [model]),
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base$/ }));
    await user.type(screen.getByRole("textbox", { name: /Search existing models/ }), "compact");
    await user.click(await screen.findByRole("button", { name: /^Desk base compact/ }));
    await user.click(screen.getByRole("button", { name: "Add parts (2)" }));

    expect(screen.getAllByLabelText("Part name")).toHaveLength(2);
  });
  it("retries a failed collection request", async () => {
    const user = userEvent.setup();
    const { route } = renderPickerPage({
      "GET /api/v1/collections/children": json({ detail: "offline" }, 500),
    });

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't load collections.");
    route({ ...collectionTreeRoutes([collection]) });
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("button", { name: "Parts" })).toBeVisible();
  });

  it("returns to all models through the breadcrumb", async () => {
    const user = userEvent.setup();
    renderPickerPage(
      {
        "GET /api/v1/multipart-models/7/candidates": (url) =>
          json(
            new URL(url, "http://localhost").searchParams.get("collection")
              ? [alternative]
              : [model],
          ),
      },
      aMultipart(),
      [collection],
    );

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: "Parts" }));
    expect(await screen.findByRole("button", { name: /^Desk base compact/ })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "All models" }));

    expect(await screen.findByRole("button", { name: /^Desk base$/ })).toBeVisible();
    expect(screen.getByRole("button", { name: "All models" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("shows accessible child collections without their parent", async () => {
    const user = userEvent.setup();
    renderPickerPage({}, aMultipart(), [{ ...collection, parent_id: 99 }]);

    await user.click(await screen.findByRole("button", { name: "Add a part" }));

    expect(await screen.findByRole("button", { name: "Parts" })).toBeVisible();
  });
  it("disables confirmation during the closing transition", async () => {
    const user = userEvent.setup();
    renderPickerPage();

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    await user.click(await screen.findByRole("button", { name: /^Desk base$/ }));
    const confirm = screen.getByRole("button", { name: "Add parts (1)" });
    await user.click(confirm);

    expect(confirm).toBeDisabled();
    expect(screen.getAllByLabelText("Part name")).toHaveLength(1);
  });

  it("keeps the dialog mounted through its closing transition", async () => {
    const user = userEvent.setup();
    renderPickerPage();

    await user.click(await screen.findByRole("button", { name: "Add a part" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));

    expect(dialog).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
});
