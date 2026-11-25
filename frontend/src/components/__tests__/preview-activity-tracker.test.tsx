/*
 * An administrator sees the previews the server is preparing on its own as one
 * Task, so an import that leaves many behind is not silent. It follows the full
 * backlog, finishes when generation settles, and is never kept across a reload.
 * Everyone else never reads the queue.
 */
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PreviewActivityTracker } from "@/components/preview-activity-tracker";
import * as workApi from "@/lib/api/work";
import * as taskCenter from "@/lib/task-center";
import { aPreviewActivity } from "@/test-support/factories";
import { LibraryStartupContext } from "@/lib/library-startup-context";

function mount(enabled: boolean, activityAllowed = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = (allowed: boolean) => (
    <QueryClientProvider client={client}>
      <LibraryStartupContext.Provider
        value={{
          complete: allowed,
          canLoad: () => allowed,
          request: () => {},
          settle: () => {},
        }}
      >
        <PreviewActivityTracker enabled={enabled} />
      </LibraryStartupContext.Provider>
    </QueryClientProvider>
  );
  const rendered = render(view(activityAllowed));
  return { client, allowActivity: () => rendered.rerender(view(true)) };
}

beforeEach(() => {
  window.localStorage.clear();
  taskCenter.resetTasksForNewSetup();
});

afterEach(() => {
  vi.restoreAllMocks();
  taskCenter.resetTasksForNewSetup();
});

describe("PreviewActivityTracker", () => {
  it("shows waiting previews as a running Task with its progress", async () => {
    vi.spyOn(workApi, "getPreviewActivity").mockResolvedValue(
      aPreviewActivity({ total: 100, completed: 40, remaining: 60 }),
    );

    mount(true);

    await waitFor(() => expect(taskCenter.listTasks()).toHaveLength(1));
    expect(taskCenter.listTasks()[0]).toMatchObject({
      title: "Preparing previews",
      status: "running",
      total: 100,
      processed: 40,
      detail: "100 preview tasks · 40 done · 60 remaining · 0 skipped · 0 failed",
      ambient: true,
    });
  });

  it("completes the Task with the final counts", async () => {
    const list = vi
      .spyOn(workApi, "getPreviewActivity")
      .mockResolvedValueOnce(aPreviewActivity({ total: 2, remaining: 2 }))
      .mockResolvedValue(aPreviewActivity({ total: 2, completed: 2 }));
    const { client } = mount(true);
    await waitFor(() => expect(taskCenter.listTasks()).toHaveLength(1));

    await client.invalidateQueries({ queryKey: ["work", "preview-activity"] });
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));

    await waitFor(() =>
      expect(taskCenter.listTasks()[0]).toMatchObject({
        status: "completed",
        total: 2,
        processed: 2,
        succeeded: 2,
        detail: "2 preview tasks · 2 done · 0 remaining · 0 skipped · 0 failed",
      }),
    );
  });

  it("reports failed generation instead of declaring previews ready", async () => {
    vi.spyOn(workApi, "getPreviewActivity")
      .mockResolvedValueOnce(aPreviewActivity({ total: 2, remaining: 2 }))
      .mockResolvedValue(
        aPreviewActivity({
          total: 2,
          completed: 1,
          failed: 1,
          latest_failure: { reason: "resource_limit", at: "2026-01-02T00:00:00Z" },
        }),
      );
    const { client } = mount(true);
    await waitFor(() => expect(taskCenter.listTasks()).toHaveLength(1));

    await client.invalidateQueries({ queryKey: ["work", "preview-activity"] });

    await waitFor(() =>
      expect(taskCenter.listTasks()[0]).toMatchObject({
        status: "failed",
        succeeded: 1,
        failed: 1,
      }),
    );
    expect(taskCenter.taskDetail(taskCenter.listTasks()[0])).toBe(
      "This file is too large or complex to process. You can still download it.",
    );
  });

  it("completes a rebuild when only earlier preview failures remain", async () => {
    const prior = {
      total: 2,
      failed: 1,
      latest_failure: { reason: "resource_limit", at: "2026-01-01T00:00:00Z" },
    };
    vi.spyOn(workApi, "getPreviewActivity")
      .mockResolvedValueOnce(aPreviewActivity({ ...prior, remaining: 1 }))
      .mockResolvedValue(aPreviewActivity({ ...prior, completed: 1 }));
    const { client } = mount(true);
    await waitFor(() => expect(taskCenter.listTasks()[0]?.status).toBe("running"));

    await client.invalidateQueries({ queryKey: ["work", "preview-activity"] });

    await waitFor(() => expect(taskCenter.listTasks()[0].status).toBe("completed"));
    expect(taskCenter.taskDetail(taskCenter.listTasks()[0])).toBe(
      "1 ready · 0 skipped · Earlier library failures (1)",
    );
  });

  it("reports a new failure when the library's failure count stays the same", async () => {
    vi.spyOn(workApi, "getPreviewActivity")
      .mockResolvedValueOnce(
        aPreviewActivity({
          total: 2,
          remaining: 1,
          failed: 1,
          latest_failure: { reason: "resource_limit", at: "2026-01-01T00:00:00Z" },
        }),
      )
      .mockResolvedValue(
        aPreviewActivity({
          total: 2,
          completed: 1,
          failed: 1,
          latest_failure: { reason: "storage", at: "2026-01-02T00:00:00Z" },
        }),
      );
    const { client } = mount(true);
    await waitFor(() => expect(taskCenter.listTasks()[0]?.status).toBe("running"));

    await client.invalidateQueries({ queryKey: ["work", "preview-activity"] });

    await waitFor(() => expect(taskCenter.listTasks()[0].status).toBe("failed"));
  });

  it("keeps the last counts when polling fails", async () => {
    vi.spyOn(workApi, "getPreviewActivity")
      .mockResolvedValueOnce(aPreviewActivity({ total: 2, remaining: 2 }))
      .mockRejectedValue(new Error("offline"));
    const { client } = mount(true);
    await waitFor(() => expect(taskCenter.listTasks()).toHaveLength(1));

    await client.invalidateQueries({ queryKey: ["work", "preview-activity"] });

    expect(taskCenter.listTasks()[0]).toMatchObject({ status: "running", total: 2, processed: 0 });
  });

  it("defers preview activity while Library startup is pending", () => {
    const list = vi.spyOn(workApi, "getPreviewActivity").mockResolvedValue(aPreviewActivity());

    mount(true, false);

    expect(list).not.toHaveBeenCalled();
  });

  it("loads preview activity when startup admits the read", async () => {
    vi.spyOn(workApi, "getPreviewActivity").mockResolvedValue(
      aPreviewActivity({ total: 1, remaining: 1 }),
    );
    const { allowActivity } = mount(true, false);

    allowActivity();

    await waitFor(() =>
      expect(taskCenter.listTasks()[0]).toMatchObject({
        title: "Preparing previews",
        status: "running",
        total: 1,
      }),
    );
  });

  it("reads nothing for someone who is not an administrator", () => {
    const list = vi.spyOn(workApi, "getPreviewActivity").mockResolvedValue(aPreviewActivity());

    mount(false);

    expect(list).not.toHaveBeenCalled();
    expect(taskCenter.listTasks()).toHaveLength(0);
  });
});
