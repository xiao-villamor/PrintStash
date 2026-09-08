/* The authenticated guide preserves storage recovery and shows only verified Models. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import GettingStartedPage from "@/pages/getting-started";
import { usePathname } from "@/lib/navigation";
import { aModelListItem, anExternalLibrary, anIngestJob } from "@/test-support/factories";
import {
  adminSession,
  json,
  memberSession,
  renderApp,
  type RouteTable,
} from "@/test-support/render";

import { setIngestJobSource } from "@/lib/task-center";

function Path() {
  return <span data-testid="path">{usePathname()}</span>;
}
function renderGuide(routes: RouteTable = {}, auth = adminSession()) {
  return renderApp(
    <>
      <GettingStartedPage />
      <Path />
    </>,
    {
      at: "/getting-started",
      auth,
      routes: {
        "POST /api/v1/setup/prepare-storage": json({
          ready: true,
          storage_provider: "local",
          checks: [],
        }),
        "GET /api/v1/models/page": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/libraries/locations": json([]),
        "GET /api/v1/collections": json([]),
        "GET /api/v1/tags": json([]),
        ...routes,
      },
    },
  );
}
beforeEach(() => {
  window.localStorage.clear();
});
afterEach(() => {
  setIngestJobSource(async () => []);
  vi.unstubAllGlobals();
});

describe("Getting started", () => {
  it("keeps uploads unavailable while storage needs preparation", async () => {
    renderGuide({
      "POST /api/v1/setup/prepare-storage": json({ detail: "storage_root_enrollment_failed" }, 500),
    });
    expect(await screen.findByText(/Account created; storage preparation pending/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Upload my first files" })).not.toBeInTheDocument();
  });
  it("recovers preparation without creating another account", async () => {
    const guide = renderGuide({
      "POST /api/v1/setup/prepare-storage": json({ detail: "storage_root_enrollment_failed" }, 500),
    });
    const retry = await screen.findByRole("button", { name: "Retry" });
    guide.route({
      "POST /api/v1/setup/prepare-storage": json({
        ready: true,
        storage_provider: "local",
        checks: [],
      }),
    });
    await userEvent.click(retry);
    expect(await screen.findByRole("button", { name: "Upload my first files" })).toBeVisible();
    expect(guide.requests().some((request) => request.url === "/api/v1/setup")).toBe(false);
  });
  it("lets an administrator postpone the guide", async () => {
    renderGuide();
    await userEvent.click(await screen.findByRole("button", { name: "I'll do this later" }));
    expect(screen.getByTestId("path")).toHaveTextContent(/^\/$/);
  });
  it("links to a verified Model in the library", async () => {
    renderGuide({
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ id: 42, name: "First model" })],
        total: 1,
        next_cursor: null,
      }),
    });
    expect(await screen.findByRole("link", { name: "First model" })).toHaveAttribute(
      "href",
      "/models/42",
    );
  });
  it("reports a catalog failure separately from storage preparation", async () => {
    renderGuide({ "GET /api/v1/models/page": json({ detail: "unavailable" }, 503) });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText(/storage preparation pending/)).not.toBeInTheDocument();
  });
  it("keeps non-administrators out of the guide", async () => {
    renderGuide({}, memberSession());
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent(/^\/$/));
  });
  it("sends an unauthenticated visitor to sign in", async () => {
    renderGuide({}, adminSession({ user: null }));
    await waitFor(() => expect(screen.getByTestId("path")).toHaveTextContent("/login"));
  });
  it("preselects an accessible mounted folder without enabling sources", async () => {
    renderGuide({
      "GET /api/v1/libraries/locations": json(["/libraries/models"]),
      "GET /api/v1/config": json({ external_libraries_enabled: false }),
      "GET /api/v1/libraries": json([]),
      "GET /api/v1/storage-connections": json([]),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.click(await screen.findByRole("button", { name: "/libraries/models" }));
    expect(screen.getByLabelText("Folder path on the server")).toHaveValue("/libraries/models");
    expect(screen.queryByRole("switch")).not.toBeInTheDocument();
  });
  it("prioritizes the first Model", async () => {
    renderGuide();
    expect(await screen.findByRole("button", { name: "Upload my first files" })).toBeVisible();
    expect(screen.getByRole("button", { name: /^Connect an existing folder/ })).toBeVisible();
    expect(screen.queryByRole("link", { name: "Set up a backup" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Connect a printer" })).not.toBeInTheDocument();
  });
  it("returns from folder connection with its draft", async () => {
    const guide = renderGuide();
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.type(screen.getByLabelText("Folder name"), "Workshop");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/mounted/models");
    await userEvent.click(screen.getByRole("button", { name: "Back to your first model" }));
    expect(screen.getByRole("heading", { name: "Make room for your first model" })).toHaveFocus();
    await userEvent.click(screen.getByRole("button", { name: /^Connect an existing folder/ }));
    expect(screen.getByLabelText("Folder name")).toHaveValue("Workshop");
    expect(screen.getByLabelText("Folder path on the server")).toHaveValue("/mounted/models");
    expect(
      guide.requestsWithMethod("POST").filter((r) => r.url.includes("libraries")),
    ).toHaveLength(0);
  });
  it("retries catalog loading", async () => {
    const guide = renderGuide({ "GET /api/v1/models/page": json({ detail: "unavailable" }, 503) });
    await screen.findByRole("alert");
    guide.route({
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ name: "Recovered model" })],
        total: 1,
        next_cursor: null,
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: "Recovered model" })).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(guide.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("simplifies the first upload", async () => {
    renderGuide();
    await userEvent.click(await screen.findByRole("button", { name: "Upload my first files" }));
    expect(screen.getByRole("button", { name: "Add to my library" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "From URL" })).not.toBeInTheDocument();
    expect(screen.queryByText("Tags", { exact: true })).not.toBeInTheDocument();
    await userEvent.upload(
      screen.getByLabelText("Model or G-code file"),
      new File(["solid"], "bracket.stl"),
    );
    expect(screen.getByLabelText("Model name")).toHaveValue("bracket");
    expect(screen.getByRole("button", { name: "Add to my library" })).toBeEnabled();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });
  it("shows a background upload failure", async () => {
    renderGuide({ "POST /api/v1/ingest/model": json({ detail: "unsupported_file_type" }, 400) });
    await userEvent.click(await screen.findByRole("button", { name: "Upload my first files" }));
    await userEvent.upload(
      screen.getByLabelText("Model or G-code file"),
      new File(["bad"], "bracket.stl"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add to my library" }));
    expect(await screen.findByRole("button", { name: "Choose a file to retry" })).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Choose a file to retry" }));
    expect(screen.getByRole("dialog")).toBeVisible();
  });
  it("shows background upload progress", async () => {
    let completed = false;
    setIngestJobSource(async () => [
      anIngestJob({ job_id: "guide-progress", state: completed ? "completed" : "running" }),
    ]);
    renderGuide({
      "POST /api/v1/ingest/model": json({
        job_id: "guide-progress",
        state: "pending",
        message: "queued",
      }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Upload my first files" }));
    await userEvent.upload(
      screen.getByLabelText("Model or G-code file"),
      new File(["solid"], "bracket.stl"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Add to my library" }));
    expect(await screen.findByText(/Adding your model. It will appear/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Upload my first files" })).toBeDisabled();
    completed = true;
    await waitFor(
      () => expect(screen.getByRole("button", { name: "Upload my first files" })).toBeEnabled(),
      { timeout: 5000 },
    );
  });
  it("renders verified Models as the completion state", async () => {
    renderGuide({
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ name: "Workshop bracket" })],
        total: 1,
        next_cursor: null,
      }),
    });
    expect(
      await screen.findByRole("heading", { name: "Your models are ready to explore" }),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Open my library" })).toBeVisible();
    await userEvent.click(screen.getByText("What else can I set up?"));
    expect(screen.getByRole("link", { name: "Connect a printer" })).toBeVisible();
  });
  it("connects a mounted folder in one submission", async () => {
    setIngestJobSource(async () => [anIngestJob({ job_id: "guide-folder-connect" })]);
    const guide = renderGuide({
      "GET /api/v1/config": json({ external_libraries_enabled: false }),
      "PUT /api/v1/config": json({ external_libraries_enabled: true }),
      "POST /api/v1/libraries": json(anExternalLibrary()),
      "POST /api/v1/libraries/1/scan": json({ job_id: "guide-folder-connect", state: "pending" }),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    expect(screen.getByRole("button", { name: "Connect and find models" })).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Folder name"), "My models");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/libraries/models");
    guide.route({
      "GET /api/v1/models/page": json({
        items: [aModelListItem({ name: "Indexed model" })],
        total: 1,
        next_cursor: null,
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(await screen.findByRole("link", { name: "Indexed model" })).toBeVisible();
    expect(guide.requestsWithMethod("PUT")).toMatchObject([
      { url: "/api/v1/config", body: JSON.stringify({ external_libraries_enabled: true }) },
    ]);
    const create = guide.requestsWithMethod("POST").find((r) => r.url === "/api/v1/libraries");
    expect(JSON.parse(create?.body ?? "{}")).toMatchObject({
      name: "My models",
      root_path: "/libraries/models",
      collection_mode: "mirror",
      scan_schedule: "0 * * * *",
    });
    expect(guide.requestsWithMethod("POST").filter((r) => r.url.endsWith("/scan"))).toHaveLength(1);
  });
  it("retries a scan without creating another source", async () => {
    setIngestJobSource(async () => [anIngestJob({ job_id: "guide-scan-retry" })]);
    const guide = renderGuide({
      "GET /api/v1/config": json({ external_libraries_enabled: true }),
      "POST /api/v1/libraries": json(anExternalLibrary()),
      "POST /api/v1/libraries/1/scan": json({ detail: "unavailable" }, 503),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.type(screen.getByLabelText("Folder name"), "My models");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/libraries/models");
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The folder is connected");
    expect(screen.getByLabelText("Folder path on the server")).toBeDisabled();
    guide.route({
      "POST /api/v1/libraries/1/scan": json({ job_id: "guide-scan-retry", state: "pending" }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Scan this folder again" }));
    await screen.findByText(/The scan finished, but no models/);
    expect(
      guide.requestsWithMethod("POST").filter((r) => r.url === "/api/v1/libraries"),
    ).toHaveLength(1);
    expect(guide.requestsWithMethod("POST").filter((r) => r.url.endsWith("/scan"))).toHaveLength(2);
  });
  it("retains the folder draft after connection failure", async () => {
    renderGuide({
      "GET /api/v1/config": json({ external_libraries_enabled: true }),
      "POST /api/v1/libraries": json({ detail: "library_path_not_directory" }, 400),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.type(screen.getByLabelText("Folder name"), "My models");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/missing");
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not connect this folder");
    expect(screen.getByLabelText("Folder path on the server")).toBeEnabled();
    expect(screen.getByLabelText("Folder path on the server")).toHaveValue("/missing");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/models");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
  it("explains an empty scan", async () => {
    setIngestJobSource(async () => [
      anIngestJob({ job_id: "guide-empty-scan", model_id: null, file_id: null }),
    ]);
    renderGuide({
      "GET /api/v1/config": json({ external_libraries_enabled: true }),
      "POST /api/v1/libraries": json(anExternalLibrary()),
      "POST /api/v1/libraries/1/scan": json({ job_id: "guide-empty-scan", state: "pending" }),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.type(screen.getByLabelText("Folder name"), "My models");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/libraries/models");
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(await screen.findByRole("status")).toHaveTextContent("The scan finished, but no models");
    expect(screen.getByRole("button", { name: "Back to your first model" })).toBeEnabled();
    expect(
      screen.queryByRole("heading", { name: "Your models are ready to explore" }),
    ).not.toBeInTheDocument();
  });
  it("keeps a partial scan visible for review", async () => {
    setIngestJobSource(async () => [
      anIngestJob({ job_id: "guide-partial-scan", completion: "partial", failed: 1 }),
    ]);
    const guide = renderGuide({
      "GET /api/v1/config": json({ external_libraries_enabled: true }),
      "POST /api/v1/libraries": json(anExternalLibrary()),
      "POST /api/v1/libraries/1/scan": json({ job_id: "guide-partial-scan", state: "pending" }),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.type(screen.getByLabelText("Folder name"), "My models");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/libraries/models");
    guide.route({
      "GET /api/v1/models/page": json({ items: [aModelListItem()], total: 1, next_cursor: null }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Some files could not be indexed");
    expect(screen.getByRole("button", { name: "Scan this folder again" })).toBeEnabled();
    expect(
      screen.queryByRole("heading", { name: "Your models are ready to explore" }),
    ).not.toBeInTheDocument();
  });
  it("stops connection when sources cannot be enabled", async () => {
    const guide = renderGuide({
      "GET /api/v1/config": json({ external_libraries_enabled: false }),
      "PUT /api/v1/config": json({ detail: "unavailable" }, 503),
    });
    await userEvent.click(
      await screen.findByRole("button", { name: /^Connect an existing folder/ }),
    );
    await userEvent.type(screen.getByLabelText("Folder name"), "My models");
    await userEvent.type(screen.getByLabelText("Folder path on the server"), "/libraries/models");
    await userEvent.click(screen.getByRole("button", { name: "Connect and find models" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not connect this folder");
    expect(guide.requestsWithMethod("POST").some((r) => r.url === "/api/v1/libraries")).toBe(false);
    expect(screen.getByRole("button", { name: "Connect and find models" })).toBeEnabled();
  });
});
