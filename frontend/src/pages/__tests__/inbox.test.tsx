/*
 * The pending-imports queue, which is a work list rather than a log.
 *
 * Delete is unavailable while an import is *resolving*, because the item is being
 * written to by a background job — deleting it mid-flight leaves staged bytes with
 * no row that owns them. Clearing completed imports must not touch the models they
 * produced, which is the distinction between tidying a queue and deleting a
 * library.
 *
 * The two failure rows are about not stranding the page. If a delete or a clear
 * fails while other work is still active, polling has to restart — otherwise the
 * queue freezes at whatever it was showing, and the user watches an import that
 * has actually progressed sit still until they reload.
 *
 * Localization preserves the dynamic source data, the same rule as everywhere:
 * the interface is translated, the captured values are not.
 */

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { InboxItem } from "@/types";
import { I18nProvider } from "@/lib/i18n";
import InboxPage, { type InboxPageDeps } from "@/pages/inbox";

const listPendingImports = vi.fn<InboxPageDeps["listPendingImports"]>();
const retryPendingImport = vi.fn<InboxPageDeps["retryPendingImport"]>();
const dismissPendingImport = vi.fn<InboxPageDeps["dismissPendingImport"]>();
const batchPendingImports = vi.fn<InboxPageDeps["batchPendingImports"]>();
const deps: InboxPageDeps = {
  listPendingImports,
  retryPendingImport,
  dismissPendingImport,
  batchPendingImports,
};

const pendingImport: InboxItem = {
  id: 1,
  owner_user_id: 1,
  source_kind: "url",
  source_url: "https://example.test/model",
  display_title: null,
  source_hostname: "printables.com",
  state: "review",
  manifest: { kind: "direct" },
  target_collection_id: null,
  requested_tags: [],
  background_job_id: null,
  resulting_model_id: null,
  results: [],
  error_code: null,
  retryable: false,
  attempt_count: 0,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  completed_at: null,
  completion: null,
};

describe("InboxPage", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.setItem("printstash.locale", "es");
    vi.mocked(listPendingImports).mockResolvedValue([pendingImport]);
  });

  it("localizes Inbox UI while preserving dynamic source data", async () => {
    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(
      await screen.findByRole("heading", { name: "Importaciones pendientes" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Necesita revisión")).toBeInTheDocument();
    expect(screen.getByText("printables.com")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Revisar" })).toHaveAttribute("href", "/inbox/1");
  });

  it("shows partial and failed imports with a retry action", async () => {
    localStorage.setItem("printstash.locale", "en");
    const failedImport: InboxItem = {
      ...pendingImport,
      id: 2,
      state: "failed",
      retryable: true,
      completion: "partial",
    };
    vi.mocked(listPendingImports).mockResolvedValue([pendingImport, failedImport]);
    vi.mocked(retryPendingImport).mockResolvedValue(failedImport);

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText("Partial")).toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    await retry.click();
    expect(retryPendingImport).toHaveBeenCalledWith(2);
  });

  it("uses captured metadata in an accessible work queue", async () => {
    localStorage.setItem("printstash.locale", "en");
    const richImport: InboxItem = {
      ...pendingImport,
      display_title: "Clean model by Maker | Download free STL model | Printables.com",
      manifest: {
        schema_version: 2,
        kind: "model_files",
        source: {
          provider: "printables",
          canonical_url: "https://www.printables.com/model/1-clean-model",
          source_item_id: "1",
          source_revision: null,
          adapter_version: "fixture-1",
          tags: [],
          fields: {
            title: { value: "Clean model", origin: "confirmed" },
          },
        },
        files: [
          { id: "one", name: "one.stl", file_type: "stl", size: 1_024 },
          { id: "two", name: "two.3mf", file_type: "3mf", size: 2_048 },
        ],
        selected_ids: ["one", "two"],
      },
    };
    vi.mocked(listPendingImports).mockResolvedValue([richImport]);

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    const queue = await screen.findByRole("list", { name: "Import queue" });
    expect(queue).toHaveTextContent("Clean model");
    expect(queue).toHaveTextContent("Printables");
    expect(queue).toHaveTextContent("Files: 2");
    expect(
      screen.queryByText("Clean model by Maker | Download free STL model | Printables.com"),
    ).not.toBeInTheDocument();
  });

  it("does not offer delete while an import is resolving", async () => {
    localStorage.setItem("printstash.locale", "en");
    vi.mocked(listPendingImports).mockResolvedValue([{ ...pendingImport, state: "resolving" }]);

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    expect(await screen.findByText("Resolving")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Delete import" })).not.toBeInTheDocument();
  });

  it("deletes a pending import from the queue after confirmation", async () => {
    localStorage.setItem("printstash.locale", "en");
    vi.mocked(dismissPendingImport).mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Delete import" }));
    const dialog = screen.getByRole("dialog", { name: "Delete pending import?" });
    await user.click(within(dialog).getByRole("button", { name: "Delete import" }));

    expect(dismissPendingImport).toHaveBeenCalledWith(1);
    expect(await screen.findByText("No imports in the queue")).toBeInTheDocument();
  });

  it("clears completed jobs without deleting imported models", async () => {
    localStorage.setItem("printstash.locale", "en");
    const completedImport: InboxItem = {
      ...pendingImport,
      id: 3,
      state: "completed",
      resulting_model_id: 12,
      completed_at: "2026-08-25T00:00:00Z",
      completion: "complete",
    };
    vi.mocked(listPendingImports).mockResolvedValue([completedImport]);
    vi.mocked(batchPendingImports).mockResolvedValue([]);
    const user = userEvent.setup();

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );

    await user.click(await screen.findByRole("tab", { name: /Completed/ }));
    await user.click(screen.getByRole("button", { name: "Clear completed" }));
    const dialog = screen.getByRole("dialog", { name: "Clear completed imports?" });
    expect(dialog).toHaveTextContent("Imported Models stay in your vault");
    await user.click(within(dialog).getByRole("button", { name: "Clear completed" }));

    expect(batchPendingImports).toHaveBeenCalledWith({
      item_ids: [3],
      action: "dismiss",
    });
    expect(await screen.findByText("No completed imports")).toBeInTheDocument();
  });

  it("restarts polling when deleting fails while another import is active", async () => {
    vi.useFakeTimers();
    localStorage.setItem("printstash.locale", "en");
    const activeImport: InboxItem = { ...pendingImport, id: 4, state: "captured" };
    vi.mocked(listPendingImports).mockResolvedValue([activeImport]);
    vi.mocked(dismissPendingImport).mockRejectedValue(new Error("offline"));

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("button", { name: "Delete import" }));
    const dialog = screen.getByRole("dialog", { name: "Delete pending import?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Delete import" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(dismissPendingImport).toHaveBeenCalledWith(4);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(listPendingImports).toHaveBeenCalledTimes(2);
  });

  it("restarts polling when clearing completed imports fails while work is active", async () => {
    vi.useFakeTimers();
    localStorage.setItem("printstash.locale", "en");
    const completedImport: InboxItem = {
      ...pendingImport,
      id: 3,
      state: "completed",
      resulting_model_id: 12,
      completed_at: "2026-08-25T00:00:00Z",
      completion: "complete",
    };
    const activeImport: InboxItem = { ...pendingImport, id: 4, state: "captured" };
    vi.mocked(listPendingImports).mockResolvedValue([completedImport, activeImport]);
    vi.mocked(batchPendingImports).mockRejectedValue(new Error("offline"));

    render(
      <I18nProvider>
        <MemoryRouter>
          <InboxPage deps={deps} />
        </MemoryRouter>
      </I18nProvider>,
    );
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    fireEvent.click(screen.getByRole("tab", { name: /Completed/ }));
    fireEvent.click(screen.getByRole("button", { name: "Clear completed" }));
    const dialog = screen.getByRole("dialog", { name: "Clear completed imports?" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Clear completed" }));
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(batchPendingImports).toHaveBeenCalledWith({
      item_ids: [3],
      action: "dismiss",
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_500);
    });
    expect(listPendingImports).toHaveBeenCalledTimes(2);
  });
});
