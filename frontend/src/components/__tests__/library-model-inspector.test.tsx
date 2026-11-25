/** Inline inspection shares real query/edit/session owners; keys must keep that command context. */
import "@testing-library/jest-dom/vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { LibraryModelInspector } from "@/components/library-model-inspector";
import { aModel, aModelListItem, aPrinter, aRevision } from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";

const file = aRevision({ id: 7, model_id: 1, gcode_revision_number: 1, is_recommended: true });
const model = aModel({ name: "Bracket", description: "Original description", files: [file] });
const printer = aPrinter({ id: 7, name: "Workshop" });
function setup(routes: RouteTable = {}, canWrite = true, motionLocked = false) {
  const onClose = vi.fn<() => void>();
  return {
    onClose,
    ...renderApp(
      <LibraryModelInspector
        id={1}
        open
        motionLocked={motionLocked}
        canWrite={canWrite}
        onClose={onClose}
      />,
      {
        routes: {
          "GET /api/v1/models/1": json(model),
          "GET /api/v1/printers": json([printer]),
          ...routes,
        },
      },
    ),
  };
}
async function ready() {
  await screen.findByRole("heading", { name: "Bracket" });
  expect(
    await screen.findByRole("combobox", { name: "Printer for printing and logging" }),
  ).toBeEnabled();
}
// Load the real lazy module before assertions, outside their DOM polling budget.
beforeAll(async () => {
  await import("@/components/library-model-inspector-content");
});
beforeEach(() => window.localStorage.clear());

describe("Library model inspector", () => {
  it("mounts the interactive details after the opening motion settles", async () => {
    const app = setup({}, true, true);
    await screen.findByRole("heading", { name: "Bracket" });
    expect(screen.queryByRole("region", { name: "Print this revision" })).not.toBeInTheDocument();

    app.rerender(<LibraryModelInspector id={1} open canWrite onClose={app.onClose} />);

    expect(await screen.findByRole("region", { name: "Print this revision" })).toBeVisible();
  });

  it("keeps a cover out of the opening placeholder", async () => {
    const cover = "/api/v1/models/1/cover/content";
    renderApp(
      <LibraryModelInspector
        id={1}
        open
        motionLocked
        summary={aModelListItem({ name: model.name, thumbnail_url: cover })}
        canWrite
        onClose={vi.fn<() => void>()}
      />,
      { routes: { "GET /api/v1/models/1": json(aModel({ ...model, thumbnail_url: cover })) } },
    );

    await screen.findByRole("heading", { name: "Bracket" });

    const loading = screen.getByRole("complementary", { name: "Model details" });
    expect(loading.querySelector(".library-inspector-preview")).toBeVisible();
    expect(loading.querySelector("img")).toBeNull();
  });

  it("renders a Markdown description in the inspector", async () => {
    setup({
      "GET /api/v1/models/1": json(
        aModel({ ...model, description: "### Assembly\n\nUse **two** clips." }),
      ),
    });
    expect(await screen.findByRole("heading", { name: "Assembly" })).toBeVisible();
    expect(screen.getByText("two").tagName).toBe("STRONG");
  });

  it("shows delivery actions for the displayed revision", async () => {
    const longLabel = "WallMount".repeat(12);
    setup({
      "GET /api/v1/models/1": json(
        aModel({ ...model, files: [{ ...file, revision_label: longLabel }] }),
      ),
    });
    await ready();
    const print = within(screen.getByRole("region", { name: "Print this revision" }));
    expect(print.getByText(`Rev 1 · ${longLabel}`)).toBeVisible();
    expect(print.getByRole("button", { name: "Send to printer" })).toBeEnabled();
    expect(print.getByRole("button", { name: "Download G-code" })).toBeEnabled();
  });

  it("offers download when there is no configured printer", async () => {
    setup({ "GET /api/v1/printers": json([]) });
    await screen.findByRole("heading", { name: "Bracket" });
    expect(screen.getByRole("button", { name: "Download G-code" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Send to printer" })).toBeNull();
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
    expect(
      app.requestsWithMethod("POST").filter((request) => request.url.endsWith("/print-jobs")),
    ).toEqual([]);
  });

  it("allows download with read-only access", async () => {
    setup({}, false);
    const print = within(await screen.findByRole("region", { name: "Print this revision" }));

    expect(print.queryByRole("button", { name: "Send to printer" })).not.toBeInTheDocument();
    expect(print.getByRole("button", { name: "Download G-code" })).toBeEnabled();
    expect(screen.getByRole("link", { name: "Open page" })).toHaveAttribute("href", "/models/1");
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

    it("opens immediately when reduced motion is preferred", () => {
      setup();
      expect(panel()).toHaveAttribute("data-state", "open");
    });

    it("releases keyboard ownership when closed", async () => {
      const app = setup();
      await ready();
      app.rerender(
        <LibraryModelInspector id={1} open={false} canWrite onClose={vi.fn<() => void>()} />,
      );
      expect(panel()).toHaveAttribute("data-state", "closed");
      expect(panel()).toHaveAttribute("inert");
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
      const scroll = panel().querySelector<HTMLElement>(".library-inspector-scroll")!;
      scroll.scrollTop = 240;
      app.rerender(<LibraryModelInspector id={2} open canWrite onClose={vi.fn<() => void>()} />);
      await screen.findByRole("heading", { name: "Hinge" });
      expect(scroll.scrollTop).toBe(0);
    });
  });
});
