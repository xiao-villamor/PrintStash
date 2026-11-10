/** Cards expose real revision evidence and commands, without treating unavailable history as empty. */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ModelCardRevision } from "@/components/model-card-revision";
import { aModel, aModelListItem, aModelPrintJob, aRevision } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { ModelRead } from "@/types";

const recommended = aRevision({
  id: 7,
  version: 4,
  gcode_revision_number: 2,
  revision_label: "Fine",
  is_recommended: true,
});
const model = aModel({
  files: [recommended, aRevision({ id: 8, version: 5, gcode_revision_number: 3 })],
});
function renderRevision(detail: ModelRead | undefined = model, options: RenderAppOptions = {}) {
  return renderApp(
    <ModelCardRevision model={aModelListItem()} detail={detail} admitted>
      <span>30m</span>
    </ModelCardRevision>,
    {
      ...options,
      routes: { "GET /api/v1/models/1/print-jobs": json([]), ...options.routes },
    },
  );
}
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("ModelCardRevision", () => {
  it("displays the recommended revision number rather than its custom label", () => {
    renderRevision();
    expect(screen.getByText("Rev 2")).toHaveAttribute("title", "Fine");
    expect(screen.getByText("Recommended")).toBeVisible();
    expect(screen.getByRole("button", { name: "Download Rev 2" })).toBeEnabled();
  });
  it("summarizes outcomes for the displayed revision", async () => {
    renderRevision(model, {
      routes: {
        "GET /api/v1/models/1/print-jobs": json([
          aModelPrintJob({ id: 1, file_id: 7, state: "completed" }),
          aModelPrintJob({ id: 2, file_id: 7, state: "completed" }),
          aModelPrintJob({ id: 3, file_id: 7, state: "failed", printer_name: "Garage" }),
          aModelPrintJob({ id: 4, file_id: 8, state: "failed", printer_name: "Other revision" }),
        ]),
      },
    });
    expect(await screen.findByText("OK 2× on Workshop")).toBeVisible();
    expect(screen.getByText("Failed 1× on Garage")).toBeVisible();
    expect(screen.queryByText(/Other revision/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Print history" })).toHaveAttribute(
      "href",
      "/models/1?tab=history",
    );
  });
  it("shows an empty print history only after it is confirmed", async () => {
    const pending = Promise.withResolvers<Response>();
    renderRevision(model, { routes: { "GET /api/v1/models/1/print-jobs": () => pending.promise } });
    expect(screen.queryByText("Not printed yet")).not.toBeInTheDocument();
    pending.resolve(json([]));
    expect(await screen.findByText("Not printed yet")).toBeVisible();
  });
  it("keeps failed print history distinct from an unprinted revision", async () => {
    const app = renderRevision(model, {
      routes: { "GET /api/v1/models/1/print-jobs": json({ detail: "unavailable" }, 503) },
    });
    await waitFor(() =>
      expect(app.requests().some((request) => request.url.endsWith("/print-jobs"))).toBe(true),
    );
    expect(screen.getByText("Print results not available")).toBeVisible();
    expect(screen.queryByText("Not printed yet")).not.toBeInTheDocument();
  });
  it("does not request privileged printer data for a member", () => {
    const app = renderRevision(model, { auth: memberSession() });
    expect(screen.getByRole("button", { name: "Print Rev 2" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Download Rev 2" })).toBeEnabled();
    expect(app.requests()).toEqual([]);
  });
  it("disables print for a read-only model", () => {
    renderRevision({ ...model, effective_role: "view" });
    expect(screen.getByRole("button", { name: "Print Rev 2" })).toBeDisabled();
  });
  it("downloads the displayed revision through authenticated transport", async () => {
    const clicked: string[] = [];
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      function (this: HTMLAnchorElement) {
        clicked.push(this.download);
      },
    );
    vi.stubGlobal(
      "URL",
      class extends URL {
        static createObjectURL() {
          return "blob:revision";
        }
        static revokeObjectURL() {}
      },
    );
    const app = renderRevision(model, {
      routes: { "GET /api/v1/files/7/download": new Response(";G-code") },
    });
    await userEvent.click(screen.getByRole("button", { name: "Download Rev 2" }));
    await waitFor(() => expect(clicked).toEqual([recommended.original_filename]));
    expect(app.requests().some((request) => request.url.endsWith("/files/7/download"))).toBe(true);
  });
  it("reports a failed download without disabling a retry", async () => {
    renderRevision(model, {
      routes: { "GET /api/v1/files/7/download": json({ detail: "File unavailable" }, 503) },
    });
    await userEvent.click(screen.getByRole("button", { name: "Download Rev 2" }));
    expect(
      await screen.findByText("This request could not be completed. Try again."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Download Rev 2" })).toBeEnabled();
  });
  it("shows the absence of G-code from the full model record", () => {
    renderRevision(aModel({ files: [aRevision({ file_type: "stl" })] }));
    expect(screen.getByText("No G-code yet")).toBeVisible();
    expect(screen.queryByRole("button", { name: /Print|Download/ })).not.toBeInTheDocument();
  });
  it("does not invent a recommended revision when none is designated", () => {
    renderRevision(aModel({ files: [aRevision({ id: 9, version: 4, gcode_revision_number: 3 })] }));
    expect(screen.getByText("Rev 3")).toBeVisible();
    expect(screen.queryByText("Recommended")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download Rev 3" })).toBeEnabled();
  });
});
