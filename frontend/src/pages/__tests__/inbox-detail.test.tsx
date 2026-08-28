/*
 * Reviewing one captured import before it becomes models.
 *
 * The destination collection defaults to the capture's own title, which is the
 * decision that makes a browser capture one click instead of three. Reusing an
 * existing collection with that title rather than creating a duplicate is the
 * other half — without it a user who imports twice from the same source ends up
 * with "Benchy" and "Benchy (2)".
 *
 * Polling stops at a terminal state and not before. An import that answers
 * "still reviewing" has to be polled again; one that finished must not be, or the
 * page keeps requesting a job that is done for as long as it stays open.
 *
 * Partial results retry *only the failed files*. Retrying everything re-downloads
 * and re-imports what already succeeded, which duplicates models.
 *
 * The source URLs here came from a third-party page, so the same scheme check as
 * the Source tab applies: safe ones become normalized links, everything else stays
 * text.
 */

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import InboxDetailPage, { type InboxDetailApi } from "@/pages/inbox-detail";
import { I18nProvider } from "@/lib/i18n";
import { defaultQueryApi, QueryApiProvider } from "@/lib/queries";
import type { CollectionRead, InboxItem } from "@/types";

const api: InboxDetailApi = {
  createCollection: vi.fn<InboxDetailApi["createCollection"]>(),
  dismissPendingImport: vi.fn<InboxDetailApi["dismissPendingImport"]>(),
  getPendingImport: vi.fn<InboxDetailApi["getPendingImport"]>(),
  importPendingImport: vi.fn<InboxDetailApi["importPendingImport"]>(),
  retryPendingImport: vi.fn<InboxDetailApi["retryPendingImport"]>(),
  updatePendingImport: vi.fn<InboxDetailApi["updatePendingImport"]>(),
};

const reviewItem: InboxItem = {
  id: 7,
  owner_user_id: 1,
  source_kind: "url",
  source_url: "https://example.test/model",
  display_title: "Calibration cube",
  source_hostname: "example.test",
  state: "review",
  manifest: {
    kind: "model_files",
    files: [{ id: "file-1", name: "cube.stl", size: 42, file_type: "stl" }],
    selected_ids: ["file-1"],
  },
  target_collection_id: null,
  requested_tags: [],
  background_job_id: "job-7",
  resulting_model_id: null,
  results: [],
  error_code: null,
  retryable: false,
  attempt_count: 1,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  completed_at: null,
  completion: null,
};

/**
 * A capture in the given state. The parameter is a plain string rather than the
 * union: this file has to cover a state a newer backend sends that this build's
 * union does not list, which is the case the fallback exists for.
 */
function itemInState(state: string): InboxItem {
  // SAFETY: `state` is the only field being varied, and every consumer of it in
  // the page either matches a known value or renders the string as it arrived.
  return { ...reviewItem, state } as InboxItem;
}

function renderPage(collections: CollectionRead[] = []) {
  return render(
    <I18nProvider>
      <QueryClientProvider client={new QueryClient()}>
        <QueryApiProvider value={{ ...defaultQueryApi, listCollections: async () => collections }}>
          <MemoryRouter initialEntries={["/inbox/7"]}>
            <Routes>
              <Route path="/inbox/:id" element={<InboxDetailPage api={api} />} />
              <Route path="/inbox" element={<p>Inbox destination</p>} />
            </Routes>
          </MemoryRouter>
        </QueryApiProvider>
      </QueryClientProvider>
    </I18nProvider>,
  );
}

describe("InboxDetailPage", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.setItem("printstash.locale", "en");
    vi.mocked(api.createCollection).mockResolvedValue({
      id: 88,
      name: "Calibration cube",
      slug: "calibration-cube",
      path: "calibration-cube",
      parent_id: null,
      model_count: 0,
      effective_role: "admin",
    });
    vi.mocked(api.dismissPendingImport).mockResolvedValue();
    vi.mocked(api.updatePendingImport).mockResolvedValue(reviewItem);
  });

  it("defaults a new collection to the capture title and creates it on import", async () => {
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);
    vi.mocked(api.importPendingImport).mockResolvedValue({
      ...reviewItem,
      state: "completed",
      completion: "complete",
    });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByRole("combobox", { name: "Destination" })).toHaveValue("new");
    expect(screen.getByRole("textbox", { name: "Collection name" })).toHaveValue(
      "Calibration cube",
    );
    expect(api.createCollection).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Import selected" }));

    await waitFor(() =>
      expect(api.createCollection).toHaveBeenCalledWith({
        name: "Calibration cube",
        parent_id: null,
      }),
    );
    expect(api.updatePendingImport).toHaveBeenCalledWith(7, {
      collection_id: 88,
      tags: [],
      selected_ids: ["file-1"],
    });
    expect(api.importPendingImport).toHaveBeenCalledWith(7, ["file-1"]);
  });

  it("reuses an existing root collection with the capture title", async () => {
    const existing: CollectionRead = {
      id: 12,
      name: "Calibration cube",
      slug: "calibration-cube",
      path: "calibration-cube",
      parent_id: null,
      model_count: 2,
      effective_role: "edit",
    };
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);
    vi.mocked(api.importPendingImport).mockResolvedValue({
      ...reviewItem,
      state: "completed",
      completion: "complete",
    });
    renderPage([existing]);

    await userEvent.setup().click(await screen.findByRole("button", { name: "Import selected" }));

    await waitFor(() => expect(api.updatePendingImport).toHaveBeenCalled());
    expect(api.createCollection).not.toHaveBeenCalled();
    expect(api.updatePendingImport).toHaveBeenCalledWith(
      7,
      expect.objectContaining({ collection_id: 12 }),
    );
  });

  it("deletes the pending item after explicit confirmation", async () => {
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete import" }));
    const dialog = screen.getByRole("dialog", { name: "Delete pending import?" });
    expect(api.dismissPendingImport).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Delete import" }));

    await waitFor(() => expect(api.dismissPendingImport).toHaveBeenCalledWith(7));
    expect(await screen.findByText("Inbox destination")).toBeInTheDocument();
  });

  it("polls after an import response still says review, then stops at a terminal state", async () => {
    const completedItem: InboxItem = { ...reviewItem, state: "completed", completion: "complete" };
    vi.mocked(api.getPendingImport)
      .mockResolvedValueOnce(reviewItem)
      .mockResolvedValueOnce(completedItem);
    vi.mocked(api.importPendingImport).mockResolvedValue(reviewItem);
    renderPage();
    await act(async () => {
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Import selected" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(api.importPendingImport).toHaveBeenCalledWith(7, ["file-1"]);

    await waitFor(() => expect(api.getPendingImport).toHaveBeenCalledTimes(2), { timeout: 2_500 });

    await new Promise((resolve) => window.setTimeout(resolve, 1_600));
    expect(api.getPendingImport).toHaveBeenCalledTimes(2);
  });

  it("shows partial results and retries only failed files", async () => {
    const partialItem: InboxItem = {
      ...reviewItem,
      state: "completed",
      completion: "partial",
      results: [
        {
          id: 11,
          source_selection_id: "file-1",
          result_key: "file-1",
          original_filename: "cube.stl",
          state: "failed",
          model_id: null,
          file_id: null,
          provenance_source_id: null,
          error_code: "unsupported_mesh",
          retryable: true,
          created_at: "2026-08-24T00:00:00Z",
          updated_at: "2026-08-24T00:00:00Z",
        },
      ],
    };
    vi.mocked(api.getPendingImport).mockResolvedValue(partialItem);
    vi.mocked(api.retryPendingImport).mockResolvedValue(partialItem);

    renderPage();

    expect(await screen.findByText("Partial")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry failed files" });
    await userEvent.setup().click(retry);
    expect(api.retryPendingImport).toHaveBeenCalledWith(7);
  });

  it("renders unsafe source URLs as plain text and normalized safe URLs as links", async () => {
    vi.mocked(api.getPendingImport).mockResolvedValue({
      ...reviewItem,
      source_url: "javascript:alert(1)",
    });
    const { unmount } = renderPage();
    expect((await screen.findByText("javascript:alert(1)")).closest("a")).toBeNull();
    unmount();

    vi.mocked(api.getPendingImport).mockResolvedValue({
      ...reviewItem,
      source_url: "HTTPS://EXAMPLE.TEST/model",
    });
    renderPage();
    expect(await screen.findByRole("link", { name: /example\.test/i })).toHaveAttribute(
      "href",
      "https://example.test/model",
    );
  });

  it("localizes Inbox detail UI while preserving captured source and file values", async () => {
    localStorage.setItem("printstash.locale", "es");
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);

    renderPage();

    expect(await screen.findByText("Calibration cube")).toBeInTheDocument();
    expect(screen.getByText("Fuente")).toBeInTheDocument();
    expect(screen.getByText("Archivos para importar")).toBeInTheDocument();
    expect(screen.getByLabelText("Seleccionar cube.stl")).toBeChecked();
    expect(screen.getByText("Destino")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("separadas por comas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Importar seleccionados" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Eliminar importación" })).toBeInTheDocument();
    expect(screen.getByText("example.test")).toBeInTheDocument();
    expect(screen.getByText("cube.stl")).toBeInTheDocument();
  });

  it("keeps the destination selector visibly focused for keyboard users", async () => {
    vi.mocked(api.getPendingImport).mockResolvedValue(reviewItem);
    const user = userEvent.setup();
    renderPage();

    const destination = await screen.findByRole("combobox", { name: "Destination" });
    for (let step = 0; step < 20 && document.activeElement !== destination; step += 1) {
      await user.tab();
    }

    expect(document.activeElement).toBe(destination);
    expect(destination).toHaveClass(
      "focus-visible:ring-ring",
      "focus-visible:ring-offset-2",
      "ring-offset-background",
    );
  });
  describe("what the capture is doing", () => {
    it.each([
      ["captured", "Captured"],
      ["resolving", "Resolving"],
      ["importing", "Importing"],
      ["failed", "Failed"],
      ["completed", "Completed"],
    ])("names the %s state in words", async (state, label) => {
      // A capture moves through five states on its own; a raw enum tells the
      // user nothing about whether to wait or to act.
      vi.mocked(api.getPendingImport).mockResolvedValue(itemInState(state));

      renderPage();

      expect(await screen.findByText(label)).toBeInTheDocument();
    });

    it("shows an unfamiliar state as it arrived", async () => {
      // A newer backend can report a state this build has no wording for, and a
      // blank badge is worse than an unfamiliar word.
      vi.mocked(api.getPendingImport).mockResolvedValue(itemInState("quarantined"));

      renderPage();

      expect(await screen.findByText("quarantined")).toBeInTheDocument();
    });
  });

  describe("where the capture came from", () => {
    it.each([
      ["cults3d.com", "Cults3D"],
      ["makerworld.com", "MakerWorld"],
      ["myminifactory.com", "MyMiniFactory"],
      ["printables.com", "Printables"],
      ["thingiverse.com", "Thingiverse"],
    ])("names %s properly", async (hostname, label) => {
      // These are brand names, and rendering "Myminifactory" reads as a bug in
      // the one place the user is checking they captured the right thing.
      vi.mocked(api.getPendingImport).mockResolvedValue({
        ...reviewItem,
        source_hostname: hostname,
      });

      renderPage();

      expect(await screen.findByText(label)).toBeInTheDocument();
    });

    it("capitalises a host it has no brand name for", async () => {
      vi.mocked(api.getPendingImport).mockResolvedValue({
        ...reviewItem,
        source_hostname: "models.example.com",
      });

      renderPage();

      expect(await screen.findByText("Example")).toBeInTheDocument();
    });

    it("says the web when there is no host at all", async () => {
      vi.mocked(api.getPendingImport).mockResolvedValue({
        ...reviewItem,
        source_url: null,
        source_hostname: null,
      });

      renderPage();

      expect(await screen.findByText("Web")).toBeInTheDocument();
    });
  });
});
