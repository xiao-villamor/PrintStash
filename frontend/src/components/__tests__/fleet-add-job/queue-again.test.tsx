/** Repeating a completed print must keep its saved Revision, plate and destination through explicit operator confirmation. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { FleetQueueAgainAction } from "@/components/fleet-add-job";
import { clearLogin } from "@/lib/auth-store";
import {
  aModel,
  aPrinter,
  aPrintJob,
  aRevision,
  FROZEN_NOW,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";
import { json, memberSession, renderApp, type RouteTable } from "@/test-support/render";
import type { CompatibilityRead, PrinterRead, PrintJobRead } from "@/types";

const printer = aPrinter({ id: 8, name: "Workshop" });
const revision = aRevision({ id: 7, model_id: 13, revision_label: "First print" });
const recommended = aRevision({
  ...revision,
  id: 9,
  revision_label: "New recommendation",
  is_recommended: true,
});
const model = aModel({
  id: 13,
  name: "Bracket",
  effective_role: "edit",
  files: [recommended, revision],
});
const completed = aPrintJob({
  id: 10,
  model_id: model.id,
  file_id: revision.id,
  printer_id: printer.id,
  state: "completed",
  started_at: FROZEN_NOW,
});
const compatibility: CompatibilityRead = {
  file_id: revision.id,
  requirements: [],
  nozzle_diameter_mm: null,
  printers: [
    {
      printer_id: printer.id,
      verdict: "unknown",
      reasons: [],
      missing_materials: [],
      color_advisories: [],
    },
  ],
};

function setup(
  job: PrintJobRead = completed,
  routes: RouteTable = {},
  destination: PrinterRead = printer,
) {
  return renderApp(<FleetQueueAgainAction job={job} printer={destination} />, {
    auth: memberSession(),
    routes: {
      "GET /api/v1/models/13": json(model),
      "POST /api/v1/fleet/compatibility": json(compatibility),
      "POST /api/v1/fleet/queue": json(aPrintJob({ ...completed, id: 11, state: "queued" })),
      ...routes,
    },
  });
}

beforeEach(() => localStorage.clear());

describe("Queue again", () => {
  it("opens the exact saved Revision without creating a job", async () => {
    const app = setup();
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue again" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Queue again" }));

    expect(await screen.findByText("Bracket · First print")).toBeVisible();
    expect(screen.getByText("Workshop")).toBeVisible();
    expect(screen.queryByText(/New recommendation/)).not.toBeInTheDocument();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("submits the saved Revision to the same printer after confirmation", async () => {
    const app = setup();
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue again" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Queue again" }));
    const save = await screen.findByRole("button", { name: "Save job" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const [check, queue] = app.requestsWithMethod("POST");
    expect(check?.url).toMatch(/\/fleet\/compatibility$/);
    expect(queue?.url).toMatch(/\/fleet\/queue$/);
    expect(JSON.parse(queue?.body ?? "{}")).toEqual({
      file_id: revision.id,
      printer_id: printer.id,
      strategy: "manual",
      target_group: null,
      priority: "normal",
      compatibility_policy: "safe",
    });
  });

  it.each([
    ["active print", aPrintJob({ ...completed, state: "printing" })],
    ["failed print", aPrintJob({ ...completed, state: "failed" })],
    ["transfer-only job", aPrintJob({ ...completed, started_at: null })],
    [
      "remote-only print",
      aPrintJob({ ...completed, source: "external", artifact_evidence: "metadata_only" }),
    ],
    [
      "captured remote archive",
      aPrintJob({ ...completed, source: "external", artifact_evidence: "gcode_archived" }),
    ],
    ["multipart build attempt", aPrintJob({ ...completed, batch_id: 2, copy_index: 1 })],
    ["different printer", aPrintJob({ ...completed, printer_id: 4 })],
  ] as const)("omits the action for a %s", (_label, job) => {
    const app = setup(job);
    expect(screen.queryByRole("button", { name: "Queue again" })).not.toBeInTheDocument();
    expect(app.requests()).toHaveLength(0);
  });

  it("omits the action for a view-only printer grant", () => {
    setup(completed, {}, aPrinter({ ...printer, access: printerAccess({ can_print: false }) }));
    expect(screen.queryByRole("button", { name: "Queue again" })).not.toBeInTheDocument();
  });

  it.each([
    ["Model access denied", json({ detail: "collection_permission_denied" }, 403)],
    ["Model missing", json({ detail: "model_not_found" }, 404)],
    ["Model view grant", json(aModel({ ...model, effective_role: "view" }))],
    ["different Model", json(aModel({ ...model, id: 14 }))],
    ["missing exact Revision", json(aModel({ ...model, files: [recommended] }))],
    [
      "wrong Revision type",
      json(aModel({ ...model, files: [aRevision({ ...revision, file_type: "stl" })] })),
    ],
    [
      "wrong Revision owner",
      json(aModel({ ...model, files: [aRevision({ ...revision, model_id: 14 })] })),
    ],
  ] as const)("disables the action with %s", async (_label, response) => {
    const app = setup(completed, { "GET /api/v1/models/13": response });
    await waitFor(() => expect(app.client.isFetching()).toBe(0));
    expect(screen.getByRole("button", { name: "Queue again" })).toBeDisabled();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("disables binary G-code for a text-only printer", async () => {
    const app = setup(
      completed,
      {
        "GET /api/v1/models/13": json(
          aModel({
            ...model,
            files: [aRevision({ ...revision, original_filename: "bracket.BGCODE" })],
          }),
        ),
      },
      aPrinter({
        ...printer,
        capabilities: printerCapabilities({ accepted_print_formats: ["gcode_text"] }),
      }),
    );
    await waitFor(() => expect(app.client.isFetching()).toBe(0));
    expect(screen.getByRole("button", { name: "Queue again" })).toBeDisabled();
  });

  it("keeps Save disabled when the saved plate no longer exists", async () => {
    setup(aPrintJob({ ...completed, plate_index: 4 }), {
      "GET /api/v1/models/13": json(
        aModel({
          ...model,
          files: [aRevision({ ...revision, original_filename: "bracket.gcode.3mf" })],
        }),
      ),
      "GET /api/v1/files/7/gcode-plates": json({
        plates: [1, 2],
        plate_metadata: [],
        identities: [],
      }),
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue again" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Queue again" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This Revision cannot be queued");
    expect(screen.getByRole("button", { name: "Save job" })).toBeDisabled();
  });

  it("keeps Save disabled if the Model grant is revoked while the dialog opens", async () => {
    const pending = Promise.withResolvers<Response>();
    let initialRead = true;
    const app = setup(completed, {
      "GET /api/v1/models/13": () => {
        if (!initialRead) return pending.promise;
        initialRead = false;
        return json(model);
      },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue again" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Queue again" }));
    expect(screen.getByRole("button", { name: "Save job" })).toBeDisabled();

    await act(async () => pending.resolve(json(aModel({ ...model, effective_role: "view" }))));
    expect(await screen.findByRole("alert")).toHaveTextContent("This Revision cannot be queued");
    expect(screen.getByRole("button", { name: "Save job" })).toBeDisabled();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("retires the confirmation when its session ends", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = setup(completed, { "POST /api/v1/fleet/compatibility": () => pending.promise });
    await waitFor(() => expect(screen.getByRole("button", { name: "Queue again" })).toBeEnabled());
    await userEvent.click(screen.getByRole("button", { name: "Queue again" }));
    const save = await screen.findByRole("button", { name: "Save job" });
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await act(async () => clearLogin());
    await act(async () => pending.resolve(json(compatibility)));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
});
