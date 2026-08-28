/*
 * The documents that live alongside a folder's models.
 *
 * A build guide, an assembly note, a supplier's datasheet — they belong to the
 * folder, so the list is scoped to it and the scoping is the part that can go
 * wrong. Navigating between folders fires overlapping requests, and a slow
 * response for the folder the user has already left must never present itself
 * as this folder's list. The spinner is therefore derived from the fetch that
 * actually completed rather than from a flag flipped on every navigation.
 *
 * Writing a document creates no row until it is saved, so "New document" opens
 * an editor rather than posting an empty one — otherwise every abandoned draft
 * leaves a blank entry behind.
 *
 * Deleting stops at a confirmation, because a document is the one thing here
 * with no copy on the user's disk.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentBrowser } from "@/components/document-browser";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { DocumentListItem } from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aDocument(over: Partial<DocumentListItem> = {}): DocumentListItem {
  return {
    id: 3,
    name: "Assembly guide",
    kind: "markdown",
    collection: "parts",
    collection_id: 1,
    filename: null,
    effective_role: "admin",
    updated_at: FROZEN_NOW,
    ...over,
  };
}

function renderBrowser(
  options: RenderAppOptions & { documents?: DocumentListItem[]; canCreate?: boolean } = {},
) {
  const { documents = [aDocument()], canCreate = true, routes = {}, ...rest } = options;
  return renderApp(
    <DocumentBrowser collectionId={1} collectionPath="parts" canCreate={canCreate} />,
    {
      routes: { "GET /api/v1/documents": json(documents), ...routes },
      ...rest,
    },
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("DocumentBrowser", () => {
  describe("listing", () => {
    it("names each document in the folder", async () => {
      renderBrowser();

      expect(await screen.findByText("Assembly guide")).toBeInTheDocument();
    });

    it("links each one to its page", async () => {
      renderBrowser();

      expect(await screen.findByRole("link", { name: /Assembly guide/ })).toHaveAttribute(
        "href",
        "/documents/3",
      );
    });

    it("says what kind of document it is", async () => {
      // A PDF and a markdown note open different editors; the badge is how the
      // user knows which they are about to get.
      renderBrowser({ documents: [aDocument({ kind: "pdf", filename: "datasheet.pdf" })] });

      expect(await screen.findByText(/pdf/)).toBeInTheDocument();
    });

    it("asks only for this folder's documents", async () => {
      // The list is folder-scoped; a request without the path returns the whole
      // vault's documents under one folder's heading.
      const { requests } = renderBrowser();

      await screen.findByText("Assembly guide");
      expect(requests().some((call) => call.url.includes("parts"))).toBe(true);
    });

    it("says so when the folder has none", async () => {
      renderBrowser({ documents: [] });

      expect(await screen.findByText("No documents here yet.")).toBeInTheDocument();
    });

    it("tells somebody who may write how to add one", async () => {
      renderBrowser({ documents: [] });

      expect(await screen.findByText("Create a markdown doc or upload a PDF.")).toBeInTheDocument();
    });

    it("keeps that hint from somebody who may not", async () => {
      renderBrowser({ documents: [], canCreate: false });

      await screen.findByText("No documents here yet.");
      expect(screen.queryByText("Create a markdown doc or upload a PDF.")).toBeNull();
    });

    it("treats a folder whose documents cannot be read as empty", async () => {
      // The folder still has its models; an error where the list should be
      // takes the whole panel away for a side feature.
      renderBrowser({
        routes: { "GET /api/v1/documents": json({ detail: "boom" }, 500) },
      });

      expect(await screen.findByText("No documents here yet.")).toBeInTheDocument();
    });
  });

  describe("adding one", () => {
    it("offers a way to write a document", async () => {
      renderBrowser();

      expect(await screen.findByRole("button", { name: /New document/ })).toBeInTheDocument();
    });

    it("creates no row until the draft is saved", async () => {
      // Posting on open leaves a blank entry behind for every abandoned draft.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderBrowser();
      await screen.findByText("Assembly guide");

      await user.click(screen.getByRole("button", { name: /New document/ }));

      expect(requestsWithMethod("POST")).toHaveLength(0);
    });

    it("uploads the file the user picked", async () => {
      const user = userEvent.setup();
      const { container, requestsWithMethod } = renderBrowser({
        routes: { "POST /api/v1/documents": json(aDocument({ id: 4, kind: "pdf" })) },
      });
      await screen.findByText("Assembly guide");

      await user.upload(
        container.ownerDocument.querySelector("input[type=file]")!,
        new File(["%PDF"], "datasheet.pdf", { type: "application/pdf" }),
      );

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("documents"))).toBe(
          true,
        ),
      );
    });

    it("surfaces an upload the server refused", async () => {
      const user = userEvent.setup();
      const { container } = renderBrowser({
        routes: { "POST /api/v1/documents": json({ detail: "unsupported_file_type" }, 415) },
      });
      await screen.findByText("Assembly guide");

      await user.upload(
        container.ownerDocument.querySelector("input[type=file]")!,
        new File(["x"], "notes.txt", { type: "text/plain" }),
      );

      expect(await screen.findByText("Unsupported file type.")).toBeInTheDocument();
    });

    it("offers nothing to add for somebody who may not write here", async () => {
      renderBrowser({ canCreate: false });

      await screen.findByText("Assembly guide");
      expect(screen.queryByRole("button", { name: /New document/ })).toBeNull();
    });
  });

  describe("deleting one", () => {
    it("asks first", async () => {
      // It is the one thing here with no copy on the user's own disk.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderBrowser();
      await screen.findByText("Assembly guide");

      await user.click(screen.getByTitle("Delete document"));

      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("deletes it once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderBrowser({
        routes: { "DELETE /api/v1/documents/3": json(null, 204) },
      });
      await screen.findByText("Assembly guide");
      await user.click(screen.getByTitle("Delete document"));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: /Delete/ }),
      );

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.endsWith("/documents/3"))).toBe(
          true,
        ),
      );
    });

    it("takes it off the list without a reload", async () => {
      const user = userEvent.setup();
      renderBrowser({
        routes: { "DELETE /api/v1/documents/3": json(null, 204) },
      });
      await screen.findByText("Assembly guide");
      await user.click(screen.getByTitle("Delete document"));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: /Delete/ }),
      );

      await waitFor(() => expect(screen.queryByText("Assembly guide")).toBeNull());
    });

    it("offers no deletion on a document the user may only read", async () => {
      renderBrowser({ documents: [aDocument({ effective_role: "view" })] });

      await screen.findByText("Assembly guide");
      expect(screen.queryByTitle("Delete document")).toBeNull();
    });
  });
});
