/** Sliced plate selection shows exactly one toolpath and never reuses another file's plate. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { GcodeViewerProps } from "@/components/gcode-viewer";
import { aRevision } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import { GcodePreview } from "../gcode-preview";

const sliced = aRevision({ id: 10, file_type: "3mf", original_filename: "project.gcode.3mf" });
const second = aRevision({ id: 11, file_type: "3mf", original_filename: "second.gcode.3mf" });
const revision = aRevision({
  id: 12,
  file_type: "gcode",
  original_filename: "part.gcode",
  is_recommended: true,
});
const bedSizeFor = () => null;

function Toolpath({ url }: GcodeViewerProps) {
  return (
    <p role="img" aria-label="Toolpath">
      {url}
    </p>
  );
}

const platesRoute = { "GET /api/v1/files/10/gcode-plates": json({ plates: [2, 10] }) };

// Each render owns a real Query client and transport; only WebGL is replaced.
function preview(files = [sliced]) {
  return <GcodePreview files={files} bedSizeFor={bedSizeFor} ToolpathViewer={Toolpath} />;
}

describe("GcodePreview", () => {
  it("defaults to the first actual sliced plate", async () => {
    renderApp(preview(), { routes: platesRoute });

    expect(await screen.findByRole("img", { name: "Toolpath" })).toHaveTextContent(
      "/files/10/embedded-gcode?plate_index=2",
    );
    expect(screen.getByRole("button", { name: "Plate 02" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("previews the selected plate", async () => {
    renderApp(preview(), { routes: platesRoute });

    await userEvent.click(await screen.findByRole("button", { name: "Plate 10" }));

    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent(
      "/files/10/embedded-gcode?plate_index=10",
    );
    expect(screen.queryByRole("button", { name: "All plates" })).not.toBeInTheDocument();
  });

  it("resets plate selection when changing files", async () => {
    renderApp(preview([sliced, second]), {
      routes: {
        ...platesRoute,
        "GET /api/v1/files/11/gcode-plates": json({ plates: [3] }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Plate 10" }));

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "G-code file" }), "11");

    expect(await screen.findByRole("button", { name: "Plate 03" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent(
      "/files/11/embedded-gcode?plate_index=3",
    );
  });

  it("follows the inspected Revision after an external selection change", () => {
    const other = aRevision({ id: 13, file_type: "gcode", original_filename: "other.gcode" });
    const view = renderApp(
      <GcodePreview
        files={[revision, other]}
        bedSizeFor={bedSizeFor}
        ToolpathViewer={Toolpath}
        selectedFileId={12}
      />,
    );
    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent("/files/12/toolpath");
    view.rerender(
      <GcodePreview
        files={[revision, other]}
        bedSizeFor={bedSizeFor}
        ToolpathViewer={Toolpath}
        selectedFileId={13}
      />,
    );
    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent("/files/13/toolpath");
  });

  it("preserves the recommended ordinary G-code preview", () => {
    const view = renderApp(preview([sliced, revision]));

    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent("/files/12/toolpath");
    expect(view.requests()).toEqual([]);
  });

  it("uses the latest ordinary G-code without a recommendation", () => {
    renderApp(
      preview([aRevision({ id: 1, file_type: "gcode" }), aRevision({ id: 2, file_type: "gcode" })]),
    );

    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent("/files/2/toolpath");
  });

  it("explains an unsliced archive", async () => {
    renderApp(preview(), { routes: { "GET /api/v1/files/10/gcode-plates": json({ plates: [] }) } });

    expect(await screen.findByText(/This 3MF has no sliced G-code/)).toBeVisible();
    expect(screen.queryByRole("img", { name: "Toolpath" })).not.toBeInTheDocument();
  });

  it("shows loading until discovery completes", async () => {
    const view = renderApp(preview(), {
      routes: {
        "GET /api/v1/files/10/gcode-plates": () => new Promise<Response>(() => {}),
      },
    });

    expect(screen.getByRole("status")).toHaveTextContent("Loading sliced plates…");
    await waitFor(() => expect(view.requests()).toHaveLength(1));
  });

  it("retries a failed discovery", async () => {
    const view = renderApp(preview(), {
      routes: {
        "GET /api/v1/files/10/gcode-plates": json({ detail: "embedded_gcode_busy" }, 429),
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn’t load sliced plates");
    view.route(platesRoute);

    await userEvent.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("img", { name: "Toolpath" })).toHaveTextContent("plate_index=2");
  });

  it("forgets a selected artifact removed from the model", async () => {
    const view = renderApp(preview([sliced, second]), {
      routes: {
        ...platesRoute,
        "GET /api/v1/files/11/gcode-plates": json({ plates: [3] }),
      },
    });
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "G-code file" }), "11");
    await screen.findByRole("button", { name: "Plate 03" });

    view.rerender(preview([sliced]));

    expect(await screen.findByRole("button", { name: "Plate 02" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("img", { name: "Toolpath" })).toHaveTextContent(
      "/files/10/embedded-gcode?plate_index=2",
    );
  });

  it("renders no preview when no supported file remains", () => {
    const view = renderApp(preview([aRevision({ file_type: "stl" })]));

    expect(screen.queryByRole("img", { name: "Toolpath" })).not.toBeInTheDocument();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(view.requests()).toEqual([]);
  });

  it("hides the previous toolpath while another archive loads", async () => {
    renderApp(preview([sliced, second]), {
      routes: {
        ...platesRoute,
        "GET /api/v1/files/11/gcode-plates": () => new Promise<Response>(() => {}),
      },
    });
    await screen.findByRole("img", { name: "Toolpath" });

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "G-code file" }), "11");

    expect(screen.getByRole("status")).toHaveTextContent("Loading sliced plates…");
    expect(screen.queryByRole("img", { name: "Toolpath" })).not.toBeInTheDocument();
  });
});
