/*
 * A document: the one thing in the library that is written here rather than
 * uploaded.
 *
 * Everything about this page turns on a single distinction — a *markdown*
 * document is editable in place and a *binary* one is only downloadable — and
 * the two share a route. Offering an editor for a PDF produces a save that
 * destroys the file; offering a download for markdown hands the user a file they
 * were trying to edit.
 *
 * `/documents/new` is the same page again with no row behind it. The document
 * exists only in this render until the first save POSTs it, so the save has to
 * *create* rather than update — a PUT against id 0 either 404s or, worse, writes
 * over whatever row that id resolves to.
 *
 * The id comes out of the URL, so it is untrusted: a route that cannot be parsed
 * has to say so rather than fetching `/documents/NaN`.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import DocumentDetailPage from "@/pages/document-detail";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { DocumentRead } from "@/types";

function aDocument(over: Partial<DocumentRead> = {}): DocumentRead {
  return {
    id: 3,
    name: "Assembly notes",
    kind: "markdown",
    collection: "parts",
    collection_id: 1,
    filename: null,
    effective_role: "edit",
    updated_at: "2026-01-01T00:00:00Z",
    body: "# Notes",
    ...over,
  };
}

function renderDocument(options: RenderAppOptions & { document?: DocumentRead } = {}) {
  const { document: doc = aDocument(), at = "/documents/3", routes = {}, ...rest } = options;
  return renderApp(<DocumentDetailPage />, {
    at,
    routePath: "/documents/:id",
    routes: {
      "GET /api/v1/documents/3": json(doc),
      ...routes,
    },
    ...rest,
  });
}

/** A pasted screenshot, which is how most images reach a document. */
function anImage() {
  return new File(["png-bytes"], "diagram.png", { type: "image/png" });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DocumentDetailPage", () => {
  describe("a markdown document", () => {
    it("shows the document's name", async () => {
      renderDocument();

      expect(await screen.findByText("Assembly notes")).toBeInTheDocument();
    });

    it("opens in preview rather than in the editor", async () => {
      // Arriving straight in a textarea invites an accidental edit to something
      // the reader only meant to consult.
      renderDocument();

      expect(await screen.findByRole("button", { name: /Edit/ })).toBeInTheDocument();
    });

    it("offers the editor to someone with write access", async () => {
      const user = userEvent.setup();
      renderDocument();

      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      expect(await screen.findByPlaceholderText(/Write markdown/)).toHaveValue("# Notes");
    });

    it("keeps the editor from a reader", async () => {
      renderDocument({
        document: aDocument({ effective_role: "view" }),
        auth: memberSession(),
      });

      await screen.findByText("Assembly notes");
      expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    });

    it("returns to preview from the editor", async () => {
      const user = userEvent.setup();
      renderDocument();
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      await user.click(screen.getByRole("button", { name: /Preview/ }));

      expect(screen.queryByPlaceholderText(/Write markdown/)).toBeNull();
    });
  });

  describe("saving an edit", () => {
    it("PUTs what the user wrote", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDocument({
        routes: { "PUT /api/v1/documents/3": json(aDocument({ body: "# Edited" })) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      const editor = await screen.findByPlaceholderText(/Write markdown/);
      await user.clear(editor);
      await user.type(editor, "# Edited");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          body: "# Edited",
        }),
      );
    });

    it("keeps the editor open when the save is refused", async () => {
      // Dropping back to preview on failure loses the edit with nothing said.
      const user = userEvent.setup();
      renderDocument({
        routes: { "PUT /api/v1/documents/3": json({ detail: "forbidden" }, 403) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      await user.click(screen.getByRole("button", { name: /Save/ }));

      expect(await screen.findByPlaceholderText(/Write markdown/)).toBeInTheDocument();
    });
  });

  describe("a new document", () => {
    it("opens straight in the editor", async () => {
      renderDocument({ at: "/documents/new" });

      expect(await screen.findByPlaceholderText(/Write markdown/)).toBeInTheDocument();
    });

    it("creates the row rather than updating one", async () => {
      // There is no row behind `/documents/new`, so a PUT would either 404 or
      // write over whatever id 0 resolves to.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDocument({
        at: "/documents/new",
        routes: { "POST /api/v1/documents": json(aDocument({ id: 9 })) },
      });
      const editor = await screen.findByPlaceholderText(/Write markdown/);
      await user.type(editor, "# Fresh");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.endsWith("/documents"))).toBe(
          true,
        ),
      );
    });

    it("files the new document in the collection the URL named", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDocument({
        at: "/documents/new?c=parts&cid=1",
        routes: { "POST /api/v1/documents": json(aDocument({ id: 9 })) },
      });
      await user.type(await screen.findByPlaceholderText(/Write markdown/), "# Fresh");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          collection_id: 1,
        }),
      );
    });
  });

  describe("a binary document", () => {
    it("offers a download rather than an editor", async () => {
      renderDocument({
        document: aDocument({ kind: "pdf", filename: "manual.pdf", body: null }),
      });

      expect(await screen.findByRole("button", { name: /Download/ })).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    });
  });

  describe("a route that names nothing", () => {
    it("says so rather than fetching an unparseable id", async () => {
      const { requests } = renderDocument({ at: "/documents/not-a-number" });

      await waitFor(() => expect(requests().some((call) => call.url.includes("NaN"))).toBe(false));
    });

    it("says so when the document is gone", async () => {
      renderDocument({
        routes: { "GET /api/v1/documents/3": json({ detail: "not_found" }, 404) },
      });

      await waitFor(() => expect(screen.queryByText("Assembly notes")).toBeNull());
    });
  });
  describe("embedding images", () => {
    it("uploads an image pasted into the editor", async () => {
      // The alternative is asking somebody to host a photo of their own printer
      // somewhere else and paste a link.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDocument({
        routes: {
          "POST /api/v1/documents/3/images": json({ url: "/api/v1/documents/3/images/1" }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByPlaceholderText(/Write markdown/), {
        clipboardData: { files: [anImage()], types: ["Files"] },
      });

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/images"))).toBe(true),
      );
    });

    it("writes the uploaded image into the markdown", async () => {
      const user = userEvent.setup();
      renderDocument({
        routes: {
          "POST /api/v1/documents/3/images": json({ url: "/api/v1/documents/3/images/1" }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByPlaceholderText(/Write markdown/), {
        clipboardData: { files: [anImage()], types: ["Files"] },
      });

      await waitFor(() =>
        expect(screen.getByPlaceholderText(/Write markdown/)).toHaveDisplayValue(
          /documents\/3\/images\/1/,
        ),
      );
    });

    it("uploads an image dropped on the editor", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDocument({
        routes: {
          "POST /api/v1/documents/3/images": json({ url: "/api/v1/documents/3/images/1" }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.drop(screen.getByPlaceholderText(/Write markdown/), {
        dataTransfer: { files: [anImage()], types: ["Files"] },
      });

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/images"))).toBe(true),
      );
    });

    it("refuses to attach an image to a document that has no row yet", async () => {
      // The upload endpoint is keyed by document id, and an unsaved draft has
      // none — so the image would be posted at nothing.
      renderDocument({ at: "/documents/new" });
      await screen.findByPlaceholderText(/Write markdown/);

      fireEvent.paste(screen.getByPlaceholderText(/Write markdown/), {
        clipboardData: { files: [anImage()], types: ["Files"] },
      });

      expect(
        await screen.findByText("Save the document before adding images."),
      ).toBeInTheDocument();
    });

    it("ignores a pasted file that is not an image", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderDocument();
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      fireEvent.paste(screen.getByPlaceholderText(/Write markdown/), {
        clipboardData: {
          files: [new File(["x"], "notes.txt", { type: "text/plain" })],
          types: ["Files"],
        },
      });

      await waitFor(() => expect(requestsWithMethod("POST")).toHaveLength(0));
    });
  });
});
