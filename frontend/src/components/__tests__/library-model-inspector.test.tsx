/** Inline inspection shares real query/edit/session owners; keys must keep that command context. */
import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryModelInspector } from "@/components/library-model-inspector";
import { aModel, aPrinter, aRevision } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";
import { modelPrintJobsOptions } from "@/features/library/model-detail";
import type { ModelPrintJobRead } from "@/types";

const file = aRevision({ id: 7, model_id: 1, gcode_revision_number: 1, is_recommended: true });
const model = aModel({ name: "Bracket", description: "Original description", files: [file] });
const printer = aPrinter({ id: 7, name: "Workshop" });
function record(state: "completed" | "failed" = "completed"): ModelPrintJobRead {
  return {
    id: 44,
    printer_id: printer.id,
    printer_name: printer.name,
    file_id: file.id,
    remote_filename: file.original_filename,
    source: "manual",
    external_display_name: null,
    artifact_evidence: "vault",
    gcode_revision_number: 1,
    revision_label: null,
    state,
    material_type: null,
    error: null,
    filament_used_g: null,
    actual_duration_s: null,
    filament_cost: null,
    spool_id: null,
    spool_name: null,
    started_at: null,
    finished_at: null,
    created_at: "2026-06-04T00:00:00Z",
  };
}
function setup(routes: RouteTable = {}, canWrite = true) {
  const onClose = vi.fn<() => void>();
  return {
    onClose,
    ...renderApp(<LibraryModelInspector id={1} open canWrite={canWrite} onClose={onClose} />, {
      routes: {
        "GET /api/v1/models/1": json(model),
        "GET /api/v1/models/1/print-jobs": json([]),
        "GET /api/v1/printers": json([printer]),
        "POST /api/v1/models/1/print-jobs": json(record()),
        ...routes,
      },
    }),
  };
}
async function ready() {
  await screen.findByRole("heading", { name: "Bracket" });
  await waitFor(() => expect(screen.getByRole("button", { name: "OK" })).toBeEnabled());
}
beforeEach(() => window.localStorage.clear());

describe("Library model inspector", () => {
  it("logs the focused revision once when a numeric key is held", async () => {
    let created = false;
    const app = setup({
      "GET /api/v1/models/1/print-jobs": () => json(created ? [record()] : []),
      "POST /api/v1/models/1/print-jobs": () => {
        created = true;
        return json(record());
      },
    });
    await ready();
    screen.getByRole("tab", { name: "Print" }).focus();
    fireEvent.keyDown(window, { key: "1", code: "Digit1", shiftKey: true });
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    fireEvent.keyDown(window, { key: "1", code: "Digit1", repeat: true });
    await screen.findByText(/Workshop ·/);
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      file_id: 7,
      printer_id: 7,
      state: "completed",
      error: null,
    });
  });

  it("leaves numeric keys to the native input owner", async () => {
    const app = setup();
    await ready();
    fireEvent.keyDown(window, { key: "1" });
    fireEvent.keyDown(screen.getByRole("combobox", { name: "Printer for printing and logging" }), {
      key: "1",
    });
    await userEvent.click(screen.getByRole("button", { name: "Edit description" }));
    fireEvent.keyDown(screen.getByRole("textbox", { name: "Description" }), { key: "1" });
    expect(app.requestsWithMethod("POST")).toEqual([]);
  });

  it("cancels the failure draft before closing details", async () => {
    const app = setup();
    await ready();
    screen.getByRole("tab", { name: "Print" }).focus();
    fireEvent.keyDown(window, { key: "2" });
    const note = await screen.findByRole("textbox", { name: /What went wrong/ });
    await userEvent.type(note, "Corner lifted");
    fireEvent.keyDown(note, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: /What went wrong/ })).not.toBeInTheDocument();
    expect(app.onClose).not.toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(app.onClose).toHaveBeenCalledOnce();
    expect(app.requestsWithMethod("POST")).toEqual([]);
  });

  it("saves the optional failed note only through an owned Mod Enter", async () => {
    const app = setup({
      "POST /api/v1/models/1/print-jobs": json({ ...record("failed"), error: "Corner lifted" }),
    });
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "Failed" }));
    const note = screen.getByRole("textbox", { name: /What went wrong/ });
    await userEvent.type(note, "Corner lifted");
    for (const extra of [
      { isComposing: true },
      { repeat: true },
      { altKey: true },
      { shiftKey: true },
    ])
      fireEvent.keyDown(note, { key: "Enter", ctrlKey: true, ...extra });
    expect(app.requestsWithMethod("POST")).toEqual([]);
    fireEvent.keyDown(note, { key: "Enter", ctrlKey: true });
    await screen.findByText("Corner lifted");
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body)).toMatchObject({
      file_id: 7,
      printer_id: 7,
      state: "failed",
      error: "Corner lifted",
    });
  });

  it("disables outcome writes for a read-only inspector", async () => {
    const app = setup({}, false);
    await screen.findByRole("heading", { name: "Bracket" });
    expect(screen.getByRole("button", { name: "OK" })).toBeDisabled();
    screen.getByRole("tab", { name: "Print" }).focus();
    fireEvent.keyDown(window, { key: "1" });
    expect(app.requestsWithMethod("POST")).toEqual([]);
    expect(screen.getByRole("link", { name: "Open page" })).toHaveAttribute("href", "/models/1");
  });

  it("retires a late outcome receipt with its private session", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = setup({ "POST /api/v1/models/1/print-jobs": () => pending.promise });
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "OK" }));
    await act(async () => clearLogin());
    await act(async () => pending.resolve(json(record())));
    expect(screen.queryByText(/Workshop ·/)).not.toBeInTheDocument();
    expect(app.client.getQueryData(modelPrintJobsOptions(1).queryKey)).toBeUndefined();
  });

  for (const [label, status] of [
    ["a conflict", 412],
    ["an unknown outcome", 503],
  ] as const) {
    it(`requires explicit review after ${label}`, async () => {
      const app = setup({ "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, status) });
      await ready();
      await userEvent.click(screen.getByRole("button", { name: "Edit description" }));
      const text = screen.getByRole("textbox", { name: "Description" });
      await userEvent.clear(text);
      await userEvent.type(text, "My retained draft");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByRole("button", { name: "Review latest version" });
      expect(text).toHaveValue("My retained draft");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      fireEvent.keyDown(text, { key: "Enter", ctrlKey: true });
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      app.route({
        "GET /api/v1/models/1": json({
          ...model,
          edit_version: 2,
          description: "Latest server description",
        }),
      });
      await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
      await screen.findByText("Latest server description");
      expect(text).toHaveValue("My retained draft");
      app.route({
        "PATCH /api/v1/models/1": (_url, init) => {
          expect(new Headers(init?.headers).get("If-Match")).toBe(
            `"model-1-e${model.edit_epoch}-v2"`,
          );
          return json({ ...model, edit_version: 3, description: "My retained draft" });
        },
      });
      await userEvent.click(
        screen.getByRole("button", { name: "Save my draft against this version" }),
      );
      await waitFor(() =>
        expect(screen.queryByRole("textbox", { name: "Description" })).not.toBeInTheDocument(),
      );
      expect(app.requestsWithMethod("PATCH")).toHaveLength(2);
      expect(JSON.parse(app.requestsWithMethod("PATCH")[1].body)).toEqual({
        description: "My retained draft",
      });
    });
  }

  it("adopts the latest description only after the explicit use-latest choice", async () => {
    const app = setup({ "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412) });
    await ready();
    await userEvent.click(screen.getByRole("button", { name: "Edit description" }));
    const text = screen.getByRole("textbox", { name: "Description" });
    await userEvent.clear(text);
    await userEvent.type(text, "Draft");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Review latest version" });
    app.route({
      "GET /api/v1/models/1": json({
        ...model,
        edit_version: 2,
        description: "Current saved description",
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
    await userEvent.click(await screen.findByRole("button", { name: "Use latest version" }));
    expect(text).toHaveValue("Current saved description");
    expect(screen.getByRole("button", { name: "Save" })).toBeEnabled();
  });

  describe("panel motion", () => {
    const other = aModel({ id: 2, name: "Hinge", files: [] });
    const routes = {
      "GET /api/v1/models/2": json(other),
      "GET /api/v1/models/2/print-jobs": json([]),
    };
    // The panel leaves the accessibility tree once hidden, so find it by its id.
    const panel = () => {
      const element = document.getElementById("model-drawer");
      if (!element) throw new Error("the inspector panel is not mounted");
      return element;
    };

    it("settles into its open state after mounting", async () => {
      setup();
      expect(panel()).toHaveAttribute("data-state", "closed");
      await waitFor(() => expect(panel()).toHaveAttribute("data-state", "open"));
    });

    it("stays on screen while it leaves", async () => {
      const app = setup();
      await ready();
      app.rerender(
        <LibraryModelInspector id={1} open={false} canWrite onClose={vi.fn<() => void>()} />,
      );
      expect(panel()).toHaveAttribute("data-state", "closed");
      expect(panel()).not.toHaveAttribute("hidden");
    });

    it("hides once it has left", async () => {
      const app = setup();
      await ready();
      app.rerender(
        <LibraryModelInspector id={1} open={false} canWrite onClose={vi.fn<() => void>()} />,
      );
      await waitFor(() => expect(panel()).toHaveAttribute("hidden"));
    });

    it("takes no input while it leaves", async () => {
      const app = setup();
      await ready();
      app.rerender(
        <LibraryModelInspector id={1} open={false} canWrite onClose={vi.fn<() => void>()} />,
      );
      expect(panel()).toHaveAttribute("inert");
    });

    it("keeps the same panel when pointed at another model", async () => {
      const app = setup(routes);
      await ready();
      const before = panel();
      app.rerender(<LibraryModelInspector id={2} open canWrite onClose={vi.fn<() => void>()} />);
      await screen.findByRole("heading", { name: "Hinge" });
      expect(panel()).toBe(before);
      expect(panel()).toHaveAttribute("data-state", "open");
    });

    it("starts another model's details from the top", async () => {
      const app = setup(routes);
      await ready();
      panel().scrollTop = 240;
      app.rerender(<LibraryModelInspector id={2} open canWrite onClose={vi.fn<() => void>()} />);
      await screen.findByRole("heading", { name: "Hinge" });
      expect(panel().scrollTop).toBe(0);
    });
  });
});
