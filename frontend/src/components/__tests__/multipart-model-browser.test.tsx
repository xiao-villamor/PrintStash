/** Multipart browser/editor behaviour for fixed parts, alternatives, and unavailable Models. */
import "@testing-library/jest-dom/vitest";

import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useLocation } from "react-router-dom";
import { describe, expect, it } from "vitest";

import {
  MultipartModelBrowser,
  MultipartModelDetailPage,
} from "@/components/multipart-model-browser";
import { json, renderApp } from "@/test-support/render";
import type { CollectionRead, MultipartModelListItem, MultipartModelRead } from "@/types";

function aMultipart(over: Partial<MultipartModelRead> = {}): MultipartModelRead {
  return {
    id: 7,
    name: "Desk organiser",
    slug: "desk-organiser",
    description: null,
    collection: null,
    collection_id: null,
    part_count: 0,
    model_count: 0,
    guide_count: 0,
    cover_thumbnail_url: null,
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
};

function aListItem(over: Partial<MultipartModelListItem> = {}): MultipartModelListItem {
  return {
    id: 7,
    name: "Desk organiser",
    slug: "desk-organiser",
    description: null,
    collection: "parts",
    collection_id: 3,
    part_count: 2,
    model_count: 3,
    guide_count: 0,
    cover_thumbnail_url: null,
    effective_role: "admin",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function renderBrowser(
  items: MultipartModelListItem[] = [aListItem()],
  routes: Record<string, Response> = {},
) {
  return renderApp(
    <MultipartModelBrowser collection="parts" collections={[collection]} canCreate />,
    {
      routes: { "GET /api/v1/multipart-models": json(items), ...routes },
    },
  );
}

function LocationProbe() {
  return <output aria-label="current location">{useLocation().pathname}</output>;
}

describe("MultipartModelBrowser", () => {
  it("lists grouping counts", async () => {
    renderBrowser([aListItem({ guide_count: 2 })]);

    expect(await screen.findByRole("link", { name: /Desk organiser/ })).toBeVisible();
    expect(screen.getByText("2 parts")).toBeVisible();
    expect(screen.getByText("3 models")).toBeVisible();
    expect(screen.getByText(/2 guides/)).toBeVisible();
  });

  it("requests the typed search filter", async () => {
    const user = userEvent.setup();
    const { requests } = renderBrowser();

    await screen.findByRole("link", { name: /Desk organiser/ });
    await user.type(screen.getByRole("textbox", { name: /Search .*models/i }), "handle");

    await waitFor(() => {
      expect(requests().some((request) => request.url.includes("q=handle"))).toBe(true);
    });
  });

  it("sorts sets by name", async () => {
    const user = userEvent.setup();
    renderBrowser([
      aListItem({ id: 8, name: "Zebra stand" }),
      aListItem({ id: 9, name: "Adapter kit" }),
    ]);

    await screen.findByRole("link", { name: /Zebra stand/ });
    await user.selectOptions(screen.getByRole("combobox", { name: "Sort" }), "name");

    expect(
      screen
        .getAllByRole("link")
        .filter((link) => link.getAttribute("href")?.startsWith("/multipart-models/"))
        .map((link) => link.textContent),
    ).toEqual([expect.stringContaining("Adapter kit"), expect.stringContaining("Zebra stand")]);
  });

  it("shows the empty list action", async () => {
    renderBrowser([]);

    expect(await screen.findByText("No multipart models yet")).toBeVisible();
    expect(screen.getAllByRole("button", { name: "New multipart set" })).toHaveLength(2);
  });

  it("surfaces a list request error", async () => {
    renderBrowser([], { "GET /api/v1/multipart-models": json({ detail: "offline" }, 500) });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't load multipart models. Try again.",
    );
  });

  it("localizes a permission error in Spanish", async () => {
    renderApp(<MultipartModelBrowser collection="parts" collections={[collection]} canCreate />, {
      locale: "es",
      routes: {
        "GET /api/v1/multipart-models": json({ detail: "collection_permission_denied" }, 403),
      },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No tienes permiso para acceder a este modelo multiparte.",
    );
    expect(screen.queryByText("collection_permission_denied")).not.toBeInTheDocument();
  });

  it("localizes a network error in Spanish", async () => {
    renderApp(<MultipartModelBrowser collection="parts" collections={[collection]} canCreate />, {
      locale: "es",
      routes: {
        "GET /api/v1/multipart-models": () => {
          throw new TypeError("Failed to fetch");
        },
      },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se ha podido conectar con PrintStash. Comprueba el servidor e inténtalo de nuevo.",
    );
    expect(screen.queryByText("network_unreachable")).not.toBeInTheDocument();
  });

  it("uses a Spanish fallback for an unknown error detail", async () => {
    renderApp(<MultipartModelBrowser collection="parts" collections={[collection]} canCreate />, {
      locale: "es",
      routes: {
        "GET /api/v1/multipart-models": json({ detail: "unexpected_database_shape" }, 500),
      },
    });

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "No se han podido cargar los modelos multiparte. Inténtalo de nuevo.",
    );
    expect(screen.queryByText("unexpected_database_shape")).not.toBeInTheDocument();
  });

  it("validates a name before creating", async () => {
    const user = userEvent.setup();
    renderBrowser([]);

    await user.click(screen.getAllByRole("button", { name: "New multipart set" })[0]);

    expect(screen.getByRole("button", { name: "Create multipart set" })).toBeDisabled();
  });

  it("creates a grouping then opens its editor", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({ name: "New organiser" });
    const { requests, requestsWithMethod } = renderApp(
      <>
        <MultipartModelBrowser collection="parts" collections={[collection]} canCreate />
        <LocationProbe />
      </>,
      {
        routes: {
          "GET /api/v1/multipart-models": json([]),
          "POST /api/v1/multipart-models": json(detail),
        },
      },
    );

    await user.click(screen.getAllByRole("button", { name: "New multipart set" })[0]);
    await user.type(screen.getByRole("textbox", { name: "Name" }), "New organiser");
    await user.type(screen.getByRole("textbox", { name: "Description" }), "Desk accessories");
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));

    await waitFor(() => {
      expect(requests().some((request) => request.url.endsWith("/api/v1/multipart-models"))).toBe(
        true,
      );
    });
    await waitFor(() => {
      expect(screen.getByLabelText("current location")).toHaveTextContent("/multipart-models/7");
    });
    expect(JSON.parse(requestsWithMethod("POST")[0].body)).toMatchObject({
      name: "New organiser",
      description: "Desk accessories",
    });
  });

  it("keeps the form draft after a friendly create error", async () => {
    const user = userEvent.setup();
    renderApp(<MultipartModelBrowser collection="parts" collections={[collection]} canCreate />, {
      routes: {
        "GET /api/v1/multipart-models": json([]),
        "POST /api/v1/multipart-models": json({ detail: "multipart_model_invalid" }, 400),
      },
    });

    await user.click(screen.getAllByRole("button", { name: "New multipart set" })[0]);
    const name = screen.getByRole("textbox", { name: "Name" });
    await user.type(name, "My organiser");
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Couldn't create this multipart model. Check the name and try again.",
    );
    expect(name).toHaveValue("My organiser");
  });
});

describe("MultipartModelDetailPage", () => {
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
            parts: [{ id: 1, name: "Base", sort_order: 0, models: [model] }],
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
            parts: [{ id: 1, name: "Base", sort_order: 0, models: [model] }],
          }),
        ),
      },
    });

    await user.click(await screen.findByRole("button", { name: "Edit multipart set" }));

    expect(screen.getByLabelText("Part name")).toHaveValue("Base");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeVisible();
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
        { id: 1, name: "Base", sort_order: 0, models: [model] },
        { id: 2, name: "Lid", sort_order: 1, models: [alternative] },
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
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
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
            parts: [{ id: 1, name: "Base", sort_order: 0, models: [model] }],
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
            parts: [{ id: 1, name: "Base", sort_order: 0, models: [model] }],
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

    expect(screen.getByText(/Choose one/)).toBeVisible();
    expect(screen.getAllByText("Desk base compact")[0]).toBeVisible();
  });

  it("saves the edited part payload", async () => {
    const user = userEvent.setup();
    const detail = aMultipart({
      part_count: 1,
      model_count: 1,
      parts: [{ id: 1, name: "Base", sort_order: 0, models: [model] }],
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
      parts: [{ name: "Top", choices: [{ model_id: 12, choice_id: 101 }] }],
    });
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

    expect(await screen.findByText("Model unavailable")).toBeVisible();
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
      parts: [{ id: 1, name: "Base", sort_order: 0, models: [model] }],
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

    expect(await screen.findByText("Model unavailable")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Edit multipart set" }));
    await user.click(screen.getByRole("button", { name: /Remove model.*Model unavailable/ }));
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
    expect(JSON.parse(requestsWithMethod("PUT")[0].body)).toEqual({
      name: "Desk organiser",
      description: null,
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
    const picker = await screen.findByRole("list", { name: "Choose an existing model" });
    expect(picker).toBeVisible();
    // The shared modal focuses its panel before the transition mounts the
    // autofocus field; walking the native tab order reaches the action button.
    await user.tab();
    await user.tab();
    await user.tab();
    expect(screen.getByRole("button", { name: /Desk base/ })).toHaveFocus();
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
