/** Queue creation retains real revision/target context through permission, compatibility and session edges. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { FleetAddJobDialog } from "@/components/fleet-add-job";
import {
  aModel,
  aModelListItem,
  aPrinter,
  aPrintJob,
  aRevision,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";
import { json, memberSession, renderApp, type RouteTable } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";
import { queryKeys } from "@/lib/query-client";
import type { CompatibilityRead, PrinterRead } from "@/types";

const file = aRevision({ id: 7, model_id: 1, gcode_revision_number: 1, is_recommended: true });
const model = aModel({ id: 1, name: "Bracket", files: [file] });
const printer = aPrinter({ id: 8, name: "Workshop" });
function report(verdict: "compatible" | "unknown" | "mismatch" = "unknown"): CompatibilityRead {
  return {
    file_id: 7,
    requirements: [],
    nozzle_diameter_mm: null,
    printers: [
      { printer_id: 8, verdict, reasons: [], missing_materials: [], color_advisories: [] },
    ],
  };
}
function setup(routes: RouteTable = {}, printers: PrinterRead[] = [printer], member = false) {
  const onClose = vi.fn<() => void>();
  return {
    onClose,
    ...renderApp(<FleetAddJobDialog open onClose={onClose} printers={printers} />, {
      auth: member ? memberSession() : undefined,
      routes: {
        "GET /api/v1/models/page": json({
          items: [aModelListItem({ id: 1, name: "Bracket" })],
          total: 1,
          next_cursor: null,
        }),
        "GET /api/v1/models/1": json(model),
        "POST /api/v1/fleet/compatibility": json(report()),
        "POST /api/v1/fleet/queue": json(aPrintJob({ id: 9, file_id: 7, printer_id: 8 })),
        ...routes,
      },
    }),
  };
}
async function selectRevision() {
  await userEvent.click(screen.getByRole("button", { name: "Revision" }));
  await userEvent.click(await screen.findByRole("button", { name: "Bracket" }));
  await userEvent.click(await screen.findByRole("button", { name: /Revision 1/ }));
  await waitFor(() => expect(screen.getByRole("button", { name: "Save job" })).toBeEnabled());
}
beforeEach(() => localStorage.clear());

describe("Fleet Add to queue", () => {
  it("selects a revision from an additional real cursor page", async () => {
    const app = setup({
      "GET /api/v1/models/page": (url) =>
        json(
          new URL(url, "http://test").searchParams.has("cursor")
            ? { items: [aModelListItem({ id: 1, name: "Bracket" })], total: 2, next_cursor: null }
            : {
                items: [aModelListItem({ id: 2, name: "Earlier model" })],
                total: 2,
                next_cursor: "next",
              },
        ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Revision" }));
    await userEvent.click(await screen.findByRole("button", { name: "Load more" }));
    await userEvent.click(await screen.findByRole("button", { name: "Bracket" }));
    await userEvent.click(await screen.findByRole("button", { name: /Revision 1/ }));
    expect(screen.getByRole("button", { name: "Save job" })).toBeEnabled();
    const request = app
      .requests()
      .find((request) => request.method === "GET" && request.url.includes("cursor=next"));
    expect(new URL(request?.url ?? "", "http://test").searchParams.get("limit")).toBe("24");
  });

  it("keeps binary-only targets unavailable for text G-code", async () => {
    setup({}, [
      aPrinter({
        ...printer,
        capabilities: printerCapabilities({ accepted_print_formats: ["bgcode_binary"] }),
      }),
    ]);
    await userEvent.click(screen.getByRole("button", { name: "Revision" }));
    await userEvent.click(await screen.findByRole("button", { name: "Bracket" }));
    await userEvent.click(await screen.findByRole("button", { name: /Revision 1/ }));
    expect(screen.getByRole("button", { name: "Save job" })).toBeDisabled();
    expect(screen.queryByRole("option", { name: "Workshop" })).not.toBeInTheDocument();
  });

  it("sends administrator automatic routing to the existing queue owner", async () => {
    const app = setup();
    await selectRevision();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "Routing" }), "least_busy");
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    await waitFor(() => expect(app.onClose).toHaveBeenCalledOnce());
    const command = app
      .requestsWithMethod("POST")
      .find((request) => request.url.endsWith("/fleet/queue"));
    expect(JSON.parse(command?.body ?? "{}")).toMatchObject({
      file_id: 7,
      strategy: "least_busy",
      compatibility_policy: "safe",
    });
    expect(JSON.parse(command?.body ?? "{}")).not.toHaveProperty("printer_id");
  });

  it("guards a double gesture while compatibility is pending", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = setup({ "POST /api/v1/fleet/compatibility": () => pending.promise });
    await selectRevision();
    await userEvent.dblClick(screen.getByRole("button", { name: "Save job" }));
    expect(
      app
        .requestsWithMethod("POST")
        .filter((request) => request.url.endsWith("/fleet/compatibility")),
    ).toHaveLength(1);
    await act(async () => pending.resolve(json(report())));
    await waitFor(() => expect(app.onClose).toHaveBeenCalledOnce());
    expect(
      app.requestsWithMethod("POST").filter((request) => request.url.endsWith("/fleet/queue")),
    ).toHaveLength(1);
  });

  it("creates the selected real revision without requiring upload capability", async () => {
    const app = setup({}, [
      aPrinter({ ...printer, capabilities: printerCapabilities({ can_upload: false }) }),
    ]);
    app.client.setQueryData(queryKeys.fleetQueue, []);
    await selectRevision();
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    await waitFor(() => expect(app.onClose).toHaveBeenCalledOnce());
    const command = app
      .requestsWithMethod("POST")
      .find((request) => request.url.endsWith("/fleet/queue"));
    expect(app.client.getQueryState(queryKeys.fleetQueue)?.isInvalidated).toBe(true);
    expect(JSON.parse(command?.body ?? "{}")).toEqual({
      file_id: 7,
      strategy: "manual",
      printer_id: 8,
      target_group: null,
      priority: "normal",
      compatibility_policy: "safe",
    });
    expect(
      app
        .requestsWithMethod("POST")
        .filter((request) => request.url.endsWith("/fleet/compatibility")),
    ).toHaveLength(1);
  });

  it("restricts automatic routing to administrators", async () => {
    const app = setup({}, [printer], true);
    await selectRevision();
    expect(screen.queryByRole("option", { name: "Least busy" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Default printer" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    await waitFor(() => expect(app.onClose).toHaveBeenCalledOnce());
    expect(
      JSON.parse(
        app.requestsWithMethod("POST").find((request) => request.url.endsWith("/fleet/queue"))
          ?.body ?? "{}",
      ).strategy,
    ).toBe("manual");
  });

  it("blocks an absent compatibility assessment for the requested target", async () => {
    const app = setup({ "POST /api/v1/fleet/compatibility": json({ ...report(), printers: [] }) });
    await selectRevision();
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This request could not be completed. Try again.",
    );
    expect(
      app.requestsWithMethod("POST").filter((request) => request.url.endsWith("/fleet/queue")),
    ).toHaveLength(0);
    expect(app.onClose).not.toHaveBeenCalled();
  });

  it("records a material override only after the explicit policy choice", async () => {
    const app = setup({ "POST /api/v1/fleet/compatibility": json(report("mismatch")) });
    await selectRevision();
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    await screen.findByRole("alert");
    expect(
      app.requestsWithMethod("POST").some((request) => request.url.endsWith("/fleet/queue")),
    ).toBe(false);
    await userEvent.selectOptions(
      screen.getByRole("combobox", { name: "Material compatibility" }),
      "allow_mismatch",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    await waitFor(() => expect(app.onClose).toHaveBeenCalledOnce());
    expect(
      JSON.parse(
        app.requestsWithMethod("POST").find((request) => request.url.endsWith("/fleet/queue"))
          ?.body ?? "{}",
      ).compatibility_policy,
    ).toBe("allow_mismatch");
  });

  it("retires a delayed compatibility check with its session", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = setup({ "POST /api/v1/fleet/compatibility": () => pending.promise });
    await selectRevision();
    await userEvent.click(screen.getByRole("button", { name: "Save job" }));
    await act(async () => clearLogin());
    await act(async () => pending.resolve(json(report())));
    expect(
      app.requestsWithMethod("POST").some((request) => request.url.endsWith("/fleet/queue")),
    ).toBe(false);
    expect(app.onClose).not.toHaveBeenCalled();
  });

  it("leaves denied targets unavailable after choosing a revision", async () => {
    setup({}, [aPrinter({ ...printer, access: printerAccess({ can_print: false }) })]);
    await userEvent.click(screen.getByRole("button", { name: "Revision" }));
    await userEvent.click(await screen.findByRole("button", { name: "Bracket" }));
    await userEvent.click(await screen.findByRole("button", { name: /Revision 1/ }));
    expect(screen.getByRole("button", { name: "Save job" })).toBeDisabled();
    expect(screen.queryByRole("option", { name: "Workshop" })).not.toBeInTheDocument();
  });
});
