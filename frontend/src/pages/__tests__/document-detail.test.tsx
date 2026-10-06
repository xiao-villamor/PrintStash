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
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentType } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Link } from "@/lib/link";
import DocumentDetailPage from "@/pages/document-detail";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";
import { documentKeys } from "@/lib/queries/documents";
import type { DocumentRead } from "@/types";

function aDocument(over: Partial<DocumentRead> = {}): DocumentRead {
  return {
    id: 3,
    edit_version: 1,
    name: "Assembly notes",
    kind: "markdown",
    collection: "parts",
    collection_id: 1,
    multipart_model_id: null,
    filename: null,
    effective_role: "edit",
    updated_at: "2026-01-01T00:00:00Z",
    body: "# Notes",
    ...over,
  };
}

function renderDocument(
  options: RenderAppOptions & {
    document?: DocumentRead;
    pdfViewer?: ComponentType<{ file: Blob }>;
  } = {},
) {
  const {
    document: doc = aDocument(),
    pdfViewer,
    at = "/documents/3",
    routes = {},
    ...rest
  } = options;
  return renderApp(<DocumentDetailPage pdfViewer={pdfViewer} />, {
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

function PdfBytes({ file }: { file: Blob }) {
  return <output>{`PDF bytes: ${file.type}:${file.size}`}</output>;
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

    it("retains the captured version after a newer background read", async () => {
      const user = userEvent.setup();
      let conditionalHeader: string | null = null;
      const app = renderDocument({
        routes: {
          "PUT /api/v1/documents/3": (_, init) => {
            conditionalHeader = new Headers(init?.headers).get("If-Match");
            return json(aDocument({ edit_version: 3 }));
          },
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      app.route({
        "GET /api/v1/documents/3": json(aDocument({ edit_version: 2, body: "Remote" })),
      });
      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() => expect(conditionalHeader).toBe('"document-3-v1"'));
    });

    it("requires explicit review before saving a conflicting draft", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: { "PUT /api/v1/documents/3": json({ detail: "edit_conflict" }, 412) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.clear(screen.getByPlaceholderText(/Write markdown/));
      await user.type(screen.getByPlaceholderText(/Write markdown/), "My local draft");
      app.route({
        "GET /api/v1/documents/3": json(aDocument({ edit_version: 2, body: "Remote changes" })),
      });

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(await screen.findByText("Remote changes")).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("My local draft");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
      await user.click(screen.getByRole("button", { name: "Use latest version as edit base" }));
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    });

    it("confirms a lost acknowledgement through a read without repeating the write", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: {
          "PUT /api/v1/documents/3": () => {
            throw new TypeError("Failed to fetch");
          },
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.clear(screen.getByPlaceholderText(/Write markdown/));
      await user.type(screen.getByPlaceholderText(/Write markdown/), "Saved despite disconnect");
      app.route({
        "GET /api/v1/documents/3": json(
          aDocument({ edit_version: 2, body: "Saved despite disconnect" }),
        ),
      });

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(
        await screen.findByText("Save confirmed from the current document."),
      ).toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/Write markdown/)).toBeNull();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    });

    it("confirms an uncertain save after the recovery read becomes available", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: {
          "PUT /api/v1/documents/3": () => {
            throw new TypeError("Failed to fetch");
          },
        },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.type(screen.getByPlaceholderText(/Write markdown/), " recovered");
      app.route({ "GET /api/v1/documents/3": json({ detail: "offline" }, 500) });
      await user.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText(
        "The save was not acknowledged. Your draft is kept. Check the current document before trying again.",
      );
      app.route({
        "GET /api/v1/documents/3": json(aDocument({ edit_version: 2, body: "# Notes recovered" })),
      });

      await user.click(await screen.findByRole("button", { name: "Retry" }));

      expect(
        await screen.findByText("Save confirmed from the current document."),
      ).toBeInTheDocument();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    });

    it("retains an inaccessible draft without offering another write", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: { "PUT /api/v1/documents/3": json({ detail: "forbidden" }, 403) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.type(screen.getByPlaceholderText(/Write markdown/), " private draft");
      app.route({ "GET /api/v1/documents/3": json({ detail: "forbidden" }, 403) });

      await user.click(screen.getByRole("button", { name: "Save" }));

      expect(
        await screen.findByText("Document access changed. Your draft is kept here for copying."),
      ).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("# Notes private draft");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    });

    it("never dispatches a retired document gesture", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: { "PUT /api/v1/documents/3": json(aDocument({ edit_version: 2 })) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));

      await act(async () => {
        fireEvent.click(screen.getByRole("button", { name: "Save" }));
        app.unmount();
        clearLogin();
      });

      expect(app.requestsWithMethod("PUT")).toHaveLength(0);
      expect(app.client.getQueriesData({ queryKey: documentKeys.all })).toEqual([]);
    });

    it("does not publish a retired save after delayed query cancellation", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: { "PUT /api/v1/documents/3": json(aDocument({ edit_version: 2 })) },
      });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      let resume!: () => void;
      const paused = new Promise<void>((resolve) => {
        resume = resolve;
      });
      const cancel = app.client.cancelQueries.bind(app.client);
      const spy = vi
        .spyOn(app.client, "cancelQueries")
        .mockImplementationOnce(cancel)
        .mockImplementationOnce(() => paused);

      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
      await act(async () => {
        clearLogin();
        resume();
        await paused;
      });

      await waitFor(() =>
        expect(app.client.getQueriesData({ queryKey: documentKeys.all })).toEqual([]),
      );
      spy.mockRestore();
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

  describe("read recovery", () => {
    it("distinguishes document detail failure from not found", async () => {
      const user = userEvent.setup();
      const app = renderDocument({
        routes: { "GET /api/v1/documents/3": json({ detail: "offline" }, 500) },
      });
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      app.route({ "GET /api/v1/documents/3": json(aDocument()) });
      await user.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByRole("heading", { name: "Assembly notes" })).toBeInTheDocument();
    });

    it("preserves a document draft on background read", async () => {
      const user = userEvent.setup();
      const app = renderDocument();
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      const editor = screen.getByPlaceholderText(/Write markdown/);
      await user.clear(editor);
      await user.type(editor, "# My draft");
      app.route({
        "GET /api/v1/documents/3": json(
          aDocument({ name: "Server rename", body: "# Changed remotely" }),
        ),
      });
      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });
      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("# My draft");
      expect(screen.getByDisplayValue("Assembly notes")).toHaveValue("Assembly notes");
    });

    it("hides cached server content after a denied background read", async () => {
      const app = renderDocument();
      await screen.findByRole("heading", { name: "Assembly notes" });
      app.route({ "GET /api/v1/documents/3": json({ detail: "forbidden" }, 403) });

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });

      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Assembly notes" })).toBeNull();
      expect(screen.queryByText("Notes")).toBeNull();
      expect(screen.queryByRole("button", { name: /Edit/ })).toBeNull();
    });

    it("keeps a captured document base before the first keystroke", async () => {
      const user = userEvent.setup();
      const app = renderDocument();
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      app.route({ "GET /api/v1/documents/3": json(aDocument({ body: "# Changed remotely" })) });
      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });
      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("# Notes");
    });
  });

  describe("background freshness", () => {
    it("keeps an editor draft available after a failed refetch", async () => {
      const user = userEvent.setup();
      const app = renderDocument();
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.type(screen.getByPlaceholderText(/Write markdown/), " locally edited");
      app.route({ "GET /api/v1/documents/3": json({ detail: "offline" }, 500) });
      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });
      expect(await screen.findByRole("alert")).toBeInTheDocument();
      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("# Notes locally edited");
    });

    it("keeps a confirmed document save across a late read", async () => {
      let resolveRead!: (response: Response) => void;
      let resolveWrite!: (response: Response) => void;
      const lateRead = new Promise<Response>((resolve) => {
        resolveRead = resolve;
      });
      const write = new Promise<Response>((resolve) => {
        resolveWrite = resolve;
      });
      const user = userEvent.setup();
      const app = renderDocument({ routes: { "PUT /api/v1/documents/3": () => write } });
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      const name = screen.getByDisplayValue("Assembly notes");
      await user.clear(name);
      await user.type(name, "Saved title");
      await user.click(screen.getByRole("button", { name: "Save" }));
      await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
      app.route({ "GET /api/v1/documents/3": () => lateRead });
      const refetch = app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      await waitFor(() =>
        expect(app.requests().filter((request) => request.method === "GET")).toHaveLength(2),
      );
      await act(async () => {
        resolveWrite(json(aDocument({ name: "Saved title" })));
      });
      expect(await screen.findByRole("heading", { name: "Saved title" })).toBeInTheDocument();
      await act(async () => {
        resolveRead(json(aDocument()));
        await refetch;
      });
      expect(screen.getByRole("heading", { name: "Saved title" })).toBeInTheDocument();
    });

    it("keeps the next document draft across an earlier route save", async () => {
      let respond!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        respond = resolve;
      });
      const user = userEvent.setup();
      renderApp(
        <>
          <Link href="/documents/4">Next document</Link>
          <DocumentDetailPage />
        </>,
        {
          at: "/documents/3",
          routePath: "/documents/:id",
          routes: {
            "GET /api/v1/documents/3": json(aDocument()),
            "GET /api/v1/documents/4": json(aDocument({ id: 4, name: "Next notes" })),
            "PUT /api/v1/documents/3": () => pending,
          },
        },
      );
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.click(screen.getByRole("button", { name: "Save" }));
      await user.click(screen.getByRole("link", { name: "Next document" }));
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.type(screen.getByPlaceholderText(/Write markdown/), " next draft");

      await act(async () => {
        respond(json(aDocument({ edit_version: 2 })));
        await pending;
      });

      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("# Notes next draft");
      expect(screen.getByRole("textbox", { name: "Document name" })).toHaveValue("Next notes");
    });

    it("releases protected preview bytes after a denied read", async () => {
      vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:revoked-image");
      const revoke = vi.spyOn(URL, "revokeObjectURL");
      const app = renderDocument({
        document: aDocument({ kind: "other", filename: "diagram.png", body: null }),
        routes: {
          "GET /api/v1/documents/3/file": new Response("png", {
            headers: { "content-type": "image/png" },
          }),
        },
      });
      await screen.findByRole("img", { name: "Assembly notes" });
      app.route({ "GET /api/v1/documents/3": json({ detail: "forbidden" }, 403) });

      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });

      await screen.findByRole("alert");
      expect(screen.queryByRole("img")).toBeNull();
      expect(revoke).toHaveBeenCalledWith("blob:revoked-image");
    });

    it("keeps binary preview bytes across a metadata refresh", async () => {
      vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:guide-image");
      const revoke = vi.spyOn(URL, "revokeObjectURL");
      const binary = aDocument({ kind: "other", filename: "diagram.png", body: null });
      const app = renderDocument({
        document: binary,
        routes: {
          "GET /api/v1/documents/3/file": new Response("png", {
            headers: { "content-type": "image/png" },
          }),
        },
      });
      expect(await screen.findByRole("img", { name: "Assembly notes" })).toHaveAttribute(
        "src",
        "blob:guide-image",
      );
      app.route({ "GET /api/v1/documents/3": json({ ...binary, name: "Renamed image" }) });
      await act(async () => {
        await app.client.invalidateQueries({ queryKey: documentKeys.detail(3) });
      });
      expect(await screen.findByRole("img", { name: "Renamed image" })).toHaveAttribute(
        "src",
        "blob:guide-image",
      );
      expect(app.requests().filter((request) => request.url.endsWith("/file"))).toHaveLength(1);
      app.unmount();
      expect(revoke).toHaveBeenCalledWith("blob:guide-image");
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

    it("previews an uploaded guide image", async () => {
      vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:guide-image");
      renderDocument({
        document: aDocument({
          name: "Exploded view",
          kind: "other",
          filename: "exploded.png",
          body: null,
        }),
        routes: {
          "GET /api/v1/documents/3/file": new Response("png", {
            headers: { "content-type": "image/png" },
          }),
        },
      });

      expect(await screen.findByRole("img", { name: "Exploded view" })).toHaveAttribute(
        "src",
        "blob:guide-image",
      );
    });

    it("passes protected PDF bytes directly to the viewer", async () => {
      renderDocument({
        document: aDocument({ kind: "pdf", filename: "manual.pdf", body: null }),
        pdfViewer: PdfBytes,
        routes: {
          "GET /api/v1/documents/3/file": new Response("pdf-bytes", {
            headers: { "content-type": "application/pdf" },
          }),
        },
      });

      expect(await screen.findByText("PDF bytes: application/pdf:9")).toBeInTheDocument();
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

    it("keeps the next document draft across an earlier image upload", async () => {
      let respond!: (response: Response) => void;
      const pending = new Promise<Response>((resolve) => {
        respond = resolve;
      });
      const user = userEvent.setup();
      const app = renderApp(
        <>
          <Link href="/documents/4">Next document</Link>
          <DocumentDetailPage />
        </>,
        {
          at: "/documents/3",
          routePath: "/documents/:id",
          routes: {
            "GET /api/v1/documents/3": json(aDocument()),
            "GET /api/v1/documents/4": json(aDocument({ id: 4, name: "Next notes" })),
            "POST /api/v1/documents/3/images": () => pending,
          },
        },
      );
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      fireEvent.paste(screen.getByPlaceholderText(/Write markdown/), {
        clipboardData: { files: [anImage()] },
      });
      await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      await user.click(screen.getByRole("link", { name: "Next document" }));
      await user.click(await screen.findByRole("button", { name: /Edit/ }));
      await user.type(screen.getByPlaceholderText(/Write markdown/), " next draft");

      await act(async () => {
        respond(json({ url: "/api/v1/documents/3/images/1" }));
        await pending;
      });

      expect(screen.getByPlaceholderText(/Write markdown/)).toHaveValue("# Notes next draft");
      expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
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
