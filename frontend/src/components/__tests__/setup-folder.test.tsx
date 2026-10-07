/** First-folder gestures stop at their view/session boundary; accepted scans stay durable. */
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SetupFolder } from "@/components/setup-folder";
import { librarySourcesOptions } from "@/lib/queries/settings-library-sources";
import { clearLogin } from "@/lib/auth-store";
import { listTasks, resetTasksForNewSetup, setJobSource, syncImportJobs } from "@/lib/task-center";
import { aJob, aVaultConfig, anExternalLibrary } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";

function Sources() {
  const read = useQuery(librarySourcesOptions());
  return (
    <output aria-label="Connected sources">
      {read.data?.kind === "enabled"
        ? read.data.items.map((row) => `${row.name}:${row.root_path}`).join(",")
        : ""}
    </output>
  );
}
function setup(routes: RouteTable = {}, observer = false) {
  const indexed = vi.fn<(complete: boolean) => Promise<number | null>>().mockResolvedValue(0);
  const busy = vi.fn<(value: boolean) => void>();
  const app = renderApp(
    <>
      <SetupFolder locations={[]} onIndexed={indexed} onBusyChange={busy} />
      {observer && <Sources />}
    </>,
    {
      routes: {
        "GET /api/v1/config": json(aVaultConfig({ external_libraries_enabled: true })),
        "GET /api/v1/libraries": json([]),
        "POST /api/v1/libraries": json(
          anExternalLibrary({ name: "Workshop", root_path: "/mounted/models" }),
          201,
        ),
        "POST /api/v1/libraries/1/scan": json({ job_id: "folder-lifetime", state: "queued" }, 202),
        ...routes,
      },
    },
  );
  return { ...app, indexed, busy };
}
async function submit() {
  await userEvent.type(screen.getByLabelText("Folder name"), "Workshop");
  await userEvent.type(screen.getByLabelText("Folder path on the server"), "/mounted/models");
  await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
}
afterEach(() => {
  resetTasksForNewSetup();
  setJobSource(async () => []);
});
describe("SetupFolder", () => {
  it("stops first-folder dispatch after disposal", async () => {
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = setup({
      "GET /api/v1/config": (_url, init) => {
        signal = init?.signal;
        return held.promise;
      },
    });
    await submit();
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    await act(async () => {
      held.resolve(json(aVaultConfig({ external_libraries_enabled: false })));
      await held.promise;
    });
    expect(signal?.aborted).toBe(true);
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.indexed).not.toHaveBeenCalled();
    expect(app.busy.mock.calls).toEqual([[true]]);
  });
  it("publishes a connected folder to source observers", async () => {
    const app = setup(
      { "POST /api/v1/libraries/1/scan": json({ detail: "unavailable" }, 503) },
      true,
    );
    await submit();
    await screen.findByRole("alert");
    await waitFor(() =>
      expect(screen.getByLabelText("Connected sources")).toHaveTextContent(
        "Workshop:/mounted/models",
      ),
    );
    expect(
      app.requestsWithMethod("POST").filter((row) => row.url === "/api/v1/libraries"),
    ).toHaveLength(1);
  });
  it("suppresses completion after leaving first-folder setup", async () => {
    let completed = false;
    setJobSource(async () => [
      aJob({ job_id: "folder-lifetime", state: completed ? "completed" : "running" }),
    ]);
    const app = setup();
    await submit();
    await waitFor(() =>
      expect(listTasks().some((task) => task.jobId === "folder-lifetime")).toBe(true),
    );
    expect(listTasks().find((task) => task.jobId === "folder-lifetime")?.titleMessage).toEqual({
      key: "Scan {value1}",
      values: { value1: "Workshop" },
    });
    app.unmount();
    await act(async () => {
      completed = true;
      await syncImportJobs();
    });
    await waitFor(() =>
      expect(listTasks().find((task) => task.jobId === "folder-lifetime")?.status).toBe(
        "completed",
      ),
    );
    expect(app.indexed).not.toHaveBeenCalled();
    expect(app.busy.mock.calls).toEqual([[true]]);
  });
  it("rejects first-folder commands from a retired session", async () => {
    const app = setup();
    await userEvent.type(screen.getByLabelText("Folder name"), "Workshop");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/mounted/models");
    act(() => clearLogin());
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    expect(app.indexed).not.toHaveBeenCalled();
  });
});
