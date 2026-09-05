/* Manufacturing controls show confirmed output, preserve failed attempts, and reject stale edits. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import MultipartBuildsPage from "@/pages/multipart-builds";
import { aBuild, aBuildPart, aBuildAttempt, aModel, aRevision } from "@/test-support/factories";
import { adminSession, json, renderApp, type RouteTable } from "@/test-support/render";
import type { MultipartBuild } from "@/types/multipart-builds";

function renderBuild(build = aBuild(), extra: RouteTable = {}) {
  return renderApp(<MultipartBuildsPage />, {
    at: "/builds/1",
    routePath: "/builds/:id",
    auth: adminSession(),
    routes: {
      "GET /api/v1/multipart-builds/1": json(build),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/models/1": json(aModel({ files: [aRevision()] })),
      ...extra,
    },
  });
}
afterEach(() => vi.unstubAllGlobals());

describe("Multipart manufacturing", () => {
  it("shows one missing piece after three confirmed legs", async () => {
    renderBuild(
      aBuild({ parts: [aBuildPart({ valid_units: 3, missing_units: 1, unreserved_units: 1 })] }),
    );
    expect(await screen.findByText("1 missing")).toBeVisible();
  });
  it("keeps a draft without a revision out of the queue", async () => {
    renderBuild(aBuild({ parts: [aBuildPart({ revision_id: null, queueable: false })] }));
    expect(await screen.findByRole("button", { name: "Queue pieces" })).toBeDisabled();
  });
  it("reserves active units when proposing more work", async () => {
    renderBuild(aBuild({ parts: [aBuildPart({ active_units: 4, unreserved_units: 0 })] }));
    expect(await screen.findByRole("button", { name: "Queue pieces" })).toBeDisabled();
    expect(screen.getByText("4 missing")).toBeVisible();
  });
  it("requires explicit acceptance of extra pieces", async () => {
    renderBuild();
    const user = userEvent.setup();
    const units = await screen.findByLabelText("Pieces produced by this file");
    await user.clear(units);
    await user.type(units, "3");
    expect(screen.getByRole("button", { name: "Queue pieces" })).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: /I confirm 2 extra pieces/ }));
    expect(screen.getByRole("button", { name: "Queue pieces" })).toBeEnabled();
  });
  it("proposes zero valid pieces for a failed job", async () => {
    renderBuild(
      aBuild({
        parts: [
          aBuildPart({ attempts: [aBuildAttempt()], unreviewed_units: 4, unreserved_units: 0 }),
        ],
      }),
    );
    expect(await screen.findByLabelText("Confirmed usable")).toHaveValue(0);
    expect(screen.getByText("Job #1 · Failed")).toBeVisible();
  });
  it("proposes all planned pieces after a completed job", async () => {
    renderBuild(
      aBuild({
        parts: [
          aBuildPart({
            attempts: [aBuildAttempt({ state: "completed", suggested_valid_units: 4 })],
            unreviewed_units: 4,
            unreserved_units: 0,
          }),
        ],
      }),
    );
    expect(await screen.findByLabelText("Confirmed usable")).toHaveValue(4);
  });
  it("confirms a partial result through the versioned API", async () => {
    const updated: MultipartBuild = aBuild({
      version: 1,
      parts: [
        aBuildPart({
          valid_units: 3,
          missing_units: 1,
          unreserved_units: 1,
          attempts: [aBuildAttempt({ valid_units: 3, version: 1 })],
        }),
      ],
    });
    const app = renderBuild(
      aBuild({
        parts: [
          aBuildPart({ attempts: [aBuildAttempt()], unreviewed_units: 4, unreserved_units: 0 }),
        ],
      }),
      {
        "POST /api/v1/multipart-builds/1/attempts/1/confirm": json(updated),
      },
    );
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Confirmed usable");
    await user.clear(input);
    await user.type(input, "3");
    await user.click(screen.getByRole("button", { name: "Confirm result" }));
    expect(await screen.findByText("1 missing")).toBeVisible();
    const request = app.requests().find((item) => item.method === "POST");
    expect(JSON.parse(request?.body ?? "{}")).toMatchObject({
      version: 0,
      valid_units: 3,
      idempotency_key: expect.any(String),
    });
  });
  it("shows a concurrency conflict without losing the entered result", async () => {
    renderBuild(
      aBuild({
        parts: [
          aBuildPart({ attempts: [aBuildAttempt()], unreviewed_units: 4, unreserved_units: 0 }),
        ],
      }),
      {
        "POST /api/v1/multipart-builds/1/attempts/1/confirm": json(
          { detail: "build_result_version_conflict" },
          409,
        ),
      },
    );
    const user = userEvent.setup();
    const input = await screen.findByLabelText("Confirmed usable");
    await user.clear(input);
    await user.type(input, "3");
    await user.click(screen.getByRole("button", { name: "Confirm result" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This build changed");
    expect(input).toHaveValue(3);
  });
  it("keeps view-only users from confirming a result", async () => {
    renderBuild(
      aBuild({
        effective_role: "view",
        parts: [
          aBuildPart({ attempts: [aBuildAttempt()], unreviewed_units: 4, unreserved_units: 0 }),
        ],
      }),
    );
    expect(await screen.findByRole("button", { name: "Confirm result" })).toBeDisabled();
  });
  it("keeps archived history read-only", async () => {
    renderBuild(aBuild({ archived_at: "2026-01-01T00:00:00Z" }));
    expect(await screen.findByText("Archived build")).toBeVisible();
    expect(screen.getByRole("button", { name: "Queue pieces" })).toBeDisabled();
  });
  it("lists saved builds", async () => {
    renderApp(<MultipartBuildsPage />, {
      at: "/builds",
      routePath: "/builds",
      auth: adminSession(),
      routes: {
        "GET /api/v1/multipart-builds": json([aBuild()]),
      },
    });
    expect(await screen.findByRole("link", { name: "Kitchen table" })).toHaveAttribute(
      "href",
      "/builds/1",
    );
  });
  it("explains when no build history exists", async () => {
    renderApp(<MultipartBuildsPage />, {
      at: "/builds",
      routePath: "/builds",
      auth: adminSession(),
      routes: {
        "GET /api/v1/multipart-builds": json([]),
      },
    });
    await waitFor(() => expect(screen.getByText(/No builds here yet/)).toBeVisible());
  });
  it("queues the proposed physical pieces through the shared routing contract", async () => {
    const app = renderBuild(aBuild(), {
      "POST /api/v1/multipart-builds/1/parts/1/queue": json(
        aBuild({ version: 1, parts: [aBuildPart({ active_units: 4, unreserved_units: 0 })] }),
      ),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Queue pieces" }));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Queue pieces" })).toBeDisabled(),
    );
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      version: 0,
      units_per_job: 1,
      job_count: 4,
      confirm_excess: false,
      routing: { strategy: "least_busy" },
    });
  });
  it("changes the revision for future jobs", async () => {
    const app = renderBuild(aBuild(), {
      "GET /api/v1/models/1": json(
        aModel({ files: [aRevision(), aRevision({ id: 2, version: 2 })] }),
      ),
      "PATCH /api/v1/multipart-builds/1/parts/1": json(
        aBuild({ version: 1, parts: [aBuildPart({ revision_id: 2 })] }),
      ),
    });
    const user = userEvent.setup();
    await screen.findByRole("option", { name: /v2/ });
    await user.selectOptions(screen.getByLabelText("Revision for the next jobs"), "2");
    await waitFor(() =>
      expect(screen.getByLabelText("Revision for the next jobs")).toHaveValue("2"),
    );
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toEqual({
      version: 0,
      revision_id: 2,
    });
  });
  it("archives completed history without deleting its results", async () => {
    renderBuild(aBuild({ completed: true }), {
      "PATCH /api/v1/multipart-builds/1/archive": json(
        aBuild({ version: 1, archived_at: "2026-01-01T00:00:00Z" }),
      ),
    });
    const user = userEvent.setup();
    await screen.findByText("All required pieces confirmed");
    await user.click(screen.getByRole("button", { name: "Archive history" }));
    expect(await screen.findByText("Archived build")).toBeVisible();
  });
  it("restores archived history for another explicit edit", async () => {
    renderBuild(aBuild({ archived_at: "2026-01-01T00:00:00Z" }), {
      "PATCH /api/v1/multipart-builds/1/archive": json(aBuild({ version: 1 })),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Restore from archive" }));
    await waitFor(() => expect(screen.queryByText("Archived build")).not.toBeInTheDocument());
    expect(screen.getByRole("button", { name: "Queue pieces" })).toBeEnabled();
  });
  it("retains a duplicate's name when saving fails", async () => {
    renderBuild(aBuild(), {
      "POST /api/v1/multipart-builds/1/duplicate": json({ detail: "unavailable" }, 503),
    });
    const user = userEvent.setup();
    const name = await screen.findByLabelText("Name for the new build");
    await user.type(name, "Second table");
    await user.click(screen.getByRole("button", { name: "Duplicate build" }));
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(name).toHaveValue("Second table");
  });
  it.each([
    ["printer_permission_denied", "You do not have permission"],
    ["build_revision_required", "Select an available G-code Revision"],
    ["build_excess_confirmation_required", "Review and confirm the extra pieces"],
    ["batch_quantity_exceeds_limit", "This exceeds the queue limit"],
    ["unexpected_failure", "The action could not be completed"],
  ])("explains a rejected queue operation (%s)", async (detail, copy) => {
    renderBuild(aBuild(), {
      "POST /api/v1/multipart-builds/1/parts/1/queue": json({ detail }, 400),
    });
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Queue pieces" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
  });
  it.each(["cancelled", "unavailable", "printing"])(
    "retains the job identity when its state is %s",
    async (state) => {
      renderBuild(aBuild({ parts: [aBuildPart({ attempts: [aBuildAttempt({ state })] })] }));
      expect(await screen.findByRole("form", { name: "Job #1" })).toBeVisible();
      if (state === "printing")
        expect(screen.queryByLabelText("Confirmed usable")).not.toBeInTheDocument();
      else expect(screen.getByLabelText("Confirmed usable")).toHaveValue(0);
    },
  );
  it("offers archived history as an explicit list filter", async () => {
    const app = renderApp(<MultipartBuildsPage />, {
      at: "/builds",
      routePath: "/builds",
      auth: adminSession(),
      routes: { "GET /api/v1/multipart-builds": json([]) },
    });
    const user = userEvent.setup();
    await screen.findByText(/No builds here yet/);
    await user.click(screen.getByRole("checkbox"));
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.includes("archived=true"))).toBe(true),
    );
  });
});
