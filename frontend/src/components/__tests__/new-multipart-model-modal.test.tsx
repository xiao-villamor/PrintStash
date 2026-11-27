/** Destination search shares ZIP's picker without losing the multipart draft. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { NewMultipartModelModal } from "@/components/new-multipart-model-modal";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import {
  aCollection,
  aCollectionNode,
  aMultipartModel,
  aModelListItem,
} from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";

const parent = aCollection({ id: 1, name: "Workshop", path: "workshop", parent_id: null });
const child = aCollection({
  id: 2,
  name: "Brackets",
  path: "workshop/brackets",
  parent_id: 1,
});

function renderModal(
  collection: ReturnType<typeof aCollectionNode> | null = null,
  routes: RouteTable = {},
) {
  const onClose = vi.fn<() => void>();
  const view = renderApp(
    <NewMultipartModelModal open onClose={onClose} collection={collection} />,
    {
      routes: {
        ...collectionTreeRoutes([parent, child]),
        "POST /api/v1/multipart-models": json(aMultipartModel()),
        "GET /api/v1/models/page": json({ items: [], total: 0, next_cursor: null }),
        ...routes,
      },
    },
  );
  return { ...view, onClose };
}

describe("NewMultipartModelModal destination", () => {
  it("loads destinations only after opening the picker", async () => {
    const user = userEvent.setup();
    const view = renderModal();

    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: "Find destination" })).toBeNull(),
    );
    expect(view.requests().filter(({ url }) => url.includes("/collections/search"))).toEqual([]);
    await user.click(screen.getByRole("button", { name: "Collection" }));
    expect(await screen.findByRole("option", { name: /Workshop\/Brackets/ })).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Find destination" })).toHaveFocus();
  });

  it("creates the set in the searched destination", async () => {
    const user = userEvent.setup();
    const view = renderModal();
    await user.type(screen.getByLabelText("Name", { exact: true }), "  Desk organiser  ");
    await user.type(screen.getByLabelText("Description"), "  Printed parts  ");
    await user.click(screen.getByRole("button", { name: "Collection" }));
    await screen.findByRole("option", { name: /Workshop\/Brackets/ });
    const search = screen.getByRole("textbox", { name: "Find destination" });
    await waitFor(() => expect(search).toHaveFocus());
    await user.type(search, "brack");
    expect(search).toHaveValue("brack");
    await waitFor(() => expect(screen.queryByRole("option", { name: /^Workshop \(/ })).toBeNull());
    await user.click(await screen.findByRole("option", { name: /Workshop\/Brackets/ }));

    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: "Find destination" })).toBeNull(),
    );
    expect(screen.getByRole("button", { name: "Collection" })).toHaveTextContent("Brackets");
    expect(screen.getByText("Workshop/Brackets")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body)).toEqual({
      name: "Desk organiser",
      description: "Printed parts",
      collection_id: 2,
    });
    await waitFor(() => expect(view.onClose).toHaveBeenCalledOnce());
  });

  it("dismisses only the picker on Escape", async () => {
    const user = userEvent.setup();
    const { onClose } = renderModal();
    await user.type(screen.getByLabelText("Name", { exact: true }), "Keep this draft");
    const trigger = screen.getByRole("button", { name: "Collection" });
    await user.click(trigger);
    await screen.findByRole("textbox", { name: "Find destination" });
    await user.keyboard("{Escape}");

    await waitFor(() =>
      expect(screen.queryByRole("textbox", { name: "Find destination" })).toBeNull(),
    );
    expect(screen.getByLabelText("Name", { exact: true })).toHaveValue("Keep this draft");
    expect(trigger).toHaveFocus();
    expect(onClose).not.toHaveBeenCalled();
  });

  it("clears the initial folder through Vault only", async () => {
    const user = userEvent.setup();
    const view = renderModal(
      aCollectionNode({
        ...child,
        display_path: "Workshop/Brackets",
      }),
    );
    const trigger = screen.getByRole("button", { name: "Collection" });
    expect(trigger).toHaveTextContent("Brackets");
    await user.type(screen.getByLabelText("Name", { exact: true }), "Loose set");
    await user.click(trigger);
    const picker = screen.getByRole("dialog", { name: "Collection" });
    expect(
      await within(picker).findByRole("option", { name: /Workshop\/Brackets/ }),
    ).toHaveAttribute("aria-selected", "true");
    await user.click(within(picker).getByRole("option", { name: "Vault only" }));
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body)).toEqual({
      name: "Loose set",
      description: null,
      collection_id: null,
    });
  });

  it("creates selected models from different folders", async () => {
    const user = userEvent.setup();
    const rootModel = aModelListItem({
      id: 10,
      name: "Base",
      collection: null,
      collection_id: null,
    });
    const nestedModel = aModelListItem({
      id: 11,
      name: "Base",
      collection: "workshop/brackets",
      collection_id: 2,
      collection_label: "Workshop/Brackets",
    });
    const lid = aModelListItem({
      id: 12,
      name: "Lid",
      collection: "workshop/brackets",
      collection_id: 2,
    });
    const models = [rootModel, nestedModel, lid];
    const view = renderModal(null, {
      "GET /api/v1/models/page": (url) => {
        const params = new URL(url, "http://test").searchParams;
        const query = params.get("q");
        const items = models.filter((model) =>
          query ? model.name.includes(query) : model.collection === params.get("collection"),
        );
        return json({ items, total: items.length, next_cursor: null });
      },
    });
    await user.type(screen.getByLabelText("Name", { exact: true }), "Complete organiser");
    await user.click(await screen.findByRole("button", { name: "Base" }));
    await user.click(screen.getByRole("button", { name: "Open folder Workshop" }));
    await user.click(await screen.findByRole("button", { name: "Open folder Brackets" }));
    await screen.findByRole("button", { name: "Lid" });
    await user.click(screen.getByRole("button", { name: "Select this page" }));
    expect(screen.getByRole("status")).toHaveTextContent("3 selected");

    const search = screen.getByRole("textbox", { name: "Search existing models" });
    await user.type(search, "Base");
    await waitFor(() => expect(screen.queryByRole("button", { name: "Lid" })).toBeNull());
    expect(await screen.findAllByRole("button", { name: "Base" })).toHaveLength(2);
    for (const option of screen.getAllByRole("button", { name: "Base" })) {
      expect(option).toHaveAttribute("aria-pressed", "true");
    }
    await user.keyboard("{Enter}");
    expect(view.requestsWithMethod("POST")).toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body)).toMatchObject({
      name: "Complete organiser",
      parts: [
        { name: "Base", quantity: 1, choices: [{ model_id: 10 }] },
        { name: "Base (2)", quantity: 1, choices: [{ model_id: 11 }] },
        { name: "Lid", quantity: 1, choices: [{ model_id: 12 }] },
      ],
    });
  });

  it("reveals a complete folder page after navigation finishes", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    renderModal(null, {
      "GET /api/v1/models/page": (url) =>
        new URL(url, "http://test").searchParams.get("collection") === "workshop"
          ? pending.promise
          : json({ items: [], total: 0, next_cursor: null }),
    });
    await user.click(await screen.findByRole("button", { name: "Open folder Workshop" }));
    await screen.findByText("Loading available models…");
    expect(screen.queryByRole("button", { name: "Open folder Brackets" })).toBeNull();
    pending.resolve(
      json({
        items: [aModelListItem({ id: 10, name: "Workshop base", collection: "workshop" })],
        total: 1,
        next_cursor: null,
      }),
    );
    expect(await screen.findByRole("button", { name: "Workshop base" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Open folder Brackets" })).toBeVisible();
  });

  it("keeps selections across model pages", async () => {
    const user = userEvent.setup();
    renderModal(null, {
      "GET /api/v1/models/page": (url) => {
        const next = new URL(url, "http://test").searchParams.has("cursor");
        return json({
          items: [aModelListItem({ id: next ? 12 : 11, name: next ? "Lid" : "Base" })],
          total: 2,
          next_cursor: next ? null : "page-two",
        });
      },
    });
    await user.click(await screen.findByRole("button", { name: "Base" }));
    await user.click(screen.getByRole("button", { name: "Next" }));
    await user.click(await screen.findByRole("button", { name: "Lid" }));
    await user.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByRole("button", { name: "Base" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("status")).toHaveTextContent("2 selected");
    await user.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(screen.getByRole("status")).toHaveTextContent("0 selected");
    expect(screen.getByRole("button", { name: "Base" })).toHaveAttribute("aria-pressed", "false");
  });

  it("preserves the draft after a creation refusal", async () => {
    const user = userEvent.setup();
    const view = renderModal(null, {
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ id: 11, name: "Base" })],
        total: 1,
        next_cursor: null,
      }),
      "POST /api/v1/multipart-models": json({ detail: "collection_permission_denied" }, 403),
    });
    await user.type(screen.getByLabelText("Name", { exact: true }), "My draft");
    await user.click(await screen.findByRole("button", { name: "Base" }));
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Name", { exact: true })).toHaveValue("My draft");
    expect(screen.getByRole("button", { name: "Base" })).toHaveAttribute("aria-pressed", "true");
    expect(view.onClose).not.toHaveBeenCalled();
  });

  it("retries a failed model search", async () => {
    const user = userEvent.setup();
    const view = renderModal(null, {
      "GET /api/v1/models/page": json({ detail: "offline" }, 500),
    });
    await screen.findByRole("alert");
    view.route({
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ id: 11, name: "Recovered model" })],
        total: 1,
        next_cursor: null,
      }),
    });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("button", { name: "Recovered model" })).toBeVisible();
  });

  it("enforces the initial model limit across pages", async () => {
    const user = userEvent.setup();
    renderModal(null, {
      "GET /api/v1/models/page": (url) => {
        const offset = Number(new URL(url, "http://test").searchParams.get("cursor") ?? 0);
        const items = Array.from({ length: Math.min(24, 101 - offset) }, (_, index) =>
          aModelListItem({ id: offset + index + 1, name: "Piece " + (offset + index + 1) }),
        );
        return json({
          items,
          total: 101,
          next_cursor: offset + 24 < 101 ? String(offset + 24) : null,
        });
      },
    });
    for (let page = 0; page < 4; page++) {
      await screen.findByRole("button", { name: "Piece " + (page * 24 + 1) });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Select this page" })).toBeEnabled(),
      );
      await user.click(screen.getByRole("button", { name: "Select this page" }));
      await user.click(screen.getByRole("button", { name: "Next" }));
    }
    await screen.findByRole("button", { name: "Piece 101" });
    expect(screen.getByRole("button", { name: "Select this page" })).toBeDisabled();
    for (const id of [97, 98, 99, 100])
      await user.click(screen.getByRole("button", { name: "Piece " + id }));
    expect(screen.getByRole("status")).toHaveTextContent("100 selected");
    expect(screen.getByRole("button", { name: "Piece 101" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Piece 100" }));
    expect(screen.getByRole("button", { name: "Piece 101" })).toBeEnabled();
  });

  it("selects all descendants through the folder checkbox", async () => {
    const user = userEvent.setup();
    const outside = aModelListItem({ id: 10, name: "Outside", collection: null });
    const direct = aModelListItem({ id: 11, name: "Base", collection: "workshop" });
    const nested = aModelListItem({ id: 12, name: "Lid", collection: "workshop/brackets" });
    const view = renderModal(null, {
      "GET /api/v1/models/page": (url) => {
        const params = new URL(url, "http://test").searchParams;
        const folder = params.get("collection") === "workshop" && !params.has("direct");
        const next = params.has("cursor");
        return json({
          items: folder ? [next ? nested : direct] : [outside],
          total: folder ? 2 : 1,
          next_cursor: folder && !next ? "nested" : null,
        });
      },
    });
    await user.type(screen.getByLabelText("Name", { exact: true }), "All pieces");
    await user.click(await screen.findByRole("button", { name: "Outside" }));
    await user.click(screen.getByRole("button", { name: "Select folder Workshop" }));
    expect(await screen.findByRole("button", { name: "Deselect folder Workshop" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByText("2 of 2 models selected")).toBeVisible();
    expect(screen.getByRole("status")).toHaveTextContent("3 selected");
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body).parts).toEqual([
      { name: "Outside", quantity: 1, choices: [{ model_id: 10 }] },
      { name: "Base", quantity: 1, choices: [{ model_id: 11 }] },
      { name: "Lid", quantity: 1, choices: [{ model_id: 12 }] },
    ]);
  });

  it("deselects a folder without clearing models elsewhere", async () => {
    const user = userEvent.setup();
    const outside = aModelListItem({ id: 10, name: "Outside", collection: null });
    const pieces = [
      aModelListItem({ id: 11, name: "Base", collection: "workshop" }),
      aModelListItem({ id: 12, name: "Lid", collection: "workshop/brackets" }),
    ];
    renderModal(null, {
      "GET /api/v1/models/page": (url) => {
        const folder = new URL(url, "http://test").searchParams.has("collection");
        return json({
          items: folder ? pieces : [outside],
          total: folder ? 2 : 1,
          next_cursor: null,
        });
      },
    });
    await user.click(await screen.findByRole("button", { name: "Outside" }));
    await user.click(screen.getByRole("button", { name: "Select folder Workshop" }));
    await user.click(await screen.findByRole("button", { name: "Deselect folder Workshop" }));
    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
    expect(screen.getByRole("button", { name: "Outside" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("0 of 2 models selected")).toBeVisible();
  });

  it("keeps folder selection atomic across a failed page", async () => {
    const user = userEvent.setup();
    const outside = aModelListItem({ id: 10, name: "Outside", collection: null });
    const direct = aModelListItem({ id: 11, name: "Base", collection: "workshop" });
    const nested = aModelListItem({ id: 12, name: "Lid", collection: "workshop/brackets" });
    let failing = true;
    renderModal(null, {
      "GET /api/v1/models/page": (url) => {
        const params = new URL(url, "http://test").searchParams;
        const folder = params.has("collection");
        const next = params.has("cursor");
        if (folder && next && failing) return json({ detail: "offline" }, 500);
        return json({
          items: folder ? [next ? nested : direct] : [outside],
          total: folder ? 2 : 1,
          next_cursor: folder && !next ? "nested" : null,
        });
      },
    });
    await user.click(await screen.findByRole("button", { name: "Outside" }));
    await user.click(screen.getByRole("button", { name: "Select folder Workshop" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Your selection is unchanged");
    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
    failing = false;
    await user.click(screen.getByRole("button", { name: "Select folder Workshop" }));
    await screen.findByRole("button", { name: "Deselect folder Workshop" });
    expect(screen.getByRole("status")).toHaveTextContent("3 selected");
  });

  it("rejects a folder beyond the selection limit", async () => {
    const user = userEvent.setup();
    renderModal(null, {
      "GET /api/v1/models/page": (url) =>
        json({
          items: [],
          total: new URL(url, "http://test").searchParams.has("collection") ? 101 : 0,
          next_cursor: null,
        }),
    });
    await user.click(await screen.findByRole("button", { name: "Select folder Workshop" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("up to 100 models per set");
    expect(screen.getByRole("status")).toHaveTextContent("0 selected");
    expect(screen.getByRole("button", { name: "Select folder Workshop" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("waits for folder selection before enabling creation", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    renderModal(null, {
      "GET /api/v1/models/page": (url) =>
        new URL(url, "http://test").searchParams.has("collection")
          ? pending.promise
          : json({ items: [], total: 0, next_cursor: null }),
    });
    await user.type(screen.getByLabelText("Name", { exact: true }), "Folder set");
    await user.click(await screen.findByRole("button", { name: "Select folder Workshop" }));
    expect(screen.getByRole("button", { name: "Create multipart set" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Select folder Workshop" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    pending.resolve(
      json({
        items: [aModelListItem({ id: 11, name: "Base", collection: "workshop" })],
        total: 1,
        next_cursor: null,
      }),
    );
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Create multipart set" })).toBeEnabled(),
    );
    expect(screen.getByRole("status")).toHaveTextContent("1 selected");
  });

  it("aborts a folder selection when the popup closes", async () => {
    const user = userEvent.setup();
    const pending = Promise.withResolvers<Response>();
    const signals: AbortSignal[] = [];
    const view = renderModal(null, {
      "GET /api/v1/models/page": (url, init) => {
        if (!new URL(url, "http://test").searchParams.has("collection"))
          return json({ items: [], total: 0, next_cursor: null });
        if (init?.signal) signals.push(init.signal);
        return pending.promise;
      },
    });
    await user.click(await screen.findByRole("button", { name: "Select folder Workshop" }));
    expect(signals[0]?.aborted).toBe(false);
    view.rerender(<NewMultipartModelModal open={false} collection={null} onClose={view.onClose} />);
    await waitFor(() => expect(signals[0]?.aborted).toBe(true));
    pending.resolve(json({ items: [], total: 0, next_cursor: null }));
    await waitFor(() =>
      expect(screen.queryByRole("dialog", { name: "New multipart set" })).toBeNull(),
    );
    expect(view.requestsWithMethod("POST")).toHaveLength(0);
  });
});

describe("NewMultipartModelModal picked parts", () => {
  function renderSeeded(models: ReturnType<typeof aModelListItem>[]) {
    const onClose = vi.fn<() => void>();
    const view = renderApp(
      <NewMultipartModelModal open onClose={onClose} collection={null} initialModels={models} />,
      {
        routes: {
          ...collectionTreeRoutes([parent, child]),
          "POST /api/v1/multipart-models": json(aMultipartModel()),
          "GET /api/v1/models/page": json({ items: [], total: 0, next_cursor: null }),
        },
      },
    );
    return { ...view, onClose };
  }

  it("starts from the models handed in, listed above the browser", async () => {
    renderSeeded([
      aModelListItem({ id: 10, name: "Base", collection_label: "Workshop" }),
      aModelListItem({ id: 11, name: "Lid" }),
    ]);
    const summary = screen.getByRole("region", { name: "Parts in this set" });
    expect(within(summary).getByText("Base")).toBeVisible();
    expect(within(summary).getByText("Lid")).toBeVisible();
    expect(within(summary).getByText("2 of 100")).toBeVisible();
    expect(screen.getByText("2 selected")).toBeVisible();
  });

  it("creates the handed-in models as parts", async () => {
    const user = userEvent.setup();
    const view = renderSeeded([aModelListItem({ id: 10, name: "Base" })]);
    await user.type(screen.getByLabelText("Name", { exact: true }), "Desk organiser");
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body)).toMatchObject({
      name: "Desk organiser",
      parts: [{ name: "Base", quantity: 1, choices: [{ model_id: 10 }] }],
    });
  });

  it("drops a part when its chip is removed", async () => {
    const user = userEvent.setup();
    const view = renderSeeded([
      aModelListItem({ id: 10, name: "Base" }),
      aModelListItem({ id: 11, name: "Lid" }),
    ]);
    await user.type(screen.getByLabelText("Name", { exact: true }), "Desk organiser");
    await user.click(screen.getByRole("button", { name: "Remove Base" }));
    const summary = screen.getByRole("region", { name: "Parts in this set" });
    expect(within(summary).queryByText("Base")).toBeNull();
    expect(within(summary).getByText("1 of 100")).toBeVisible();
    expect(screen.getByText("1 selected")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body).parts).toEqual([
      { name: "Lid", quantity: 1, choices: [{ model_id: 11 }] },
    ]);
  });

  it("creates a bare set when nothing is picked", async () => {
    const user = userEvent.setup();
    const view = renderSeeded([]);
    expect(screen.getByText("No parts yet. Pick models below.")).toBeVisible();
    await user.type(screen.getByLabelText("Name", { exact: true }), "Bare");
    await user.click(screen.getByRole("button", { name: "Create multipart set" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0]!.body)).not.toHaveProperty("parts");
  });
});
