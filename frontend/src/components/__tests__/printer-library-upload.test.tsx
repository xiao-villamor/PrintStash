/** A saved Revision can reach a printer without scheduling or starting a print. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { PrinterFiles } from "@/components/printer-file-history";
import { printerKeys } from "@/features/printers/queries";
import { PrinterLibraryUpload } from "@/components/printer-library-upload";
import {
  aModel,
  aModelListItem,
  aPrinter,
  aPrintJob,
  aRevision,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";
import { json, renderApp, type RouteTable } from "@/test-support/render";

const printer = aPrinter({ id: 8, name: "Workshop" });
const receipt = aPrintJob({
  printer_id: 8,
  remote_filename: "bracket__saved.gcode",
  state: "completed",
});

function routes(filename = "bracket.gcode"): RouteTable {
  return {
    "GET /api/v1/models/page": json({
      items: [aModelListItem({ id: 1, name: "Bracket" })],
      total: 1,
      next_cursor: null,
    }),
    "GET /api/v1/models/1": json(
      aModel({
        id: 1,
        name: "Bracket",
        files: [
          aRevision({
            id: 7,
            model_id: 1,
            gcode_revision_number: 1,
            original_filename: filename,
          }),
        ],
      }),
    ),
    "POST /api/v1/printers/8/send": json(receipt),
  };
}

async function choose(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Revision" }));
  await user.click(await screen.findByRole("button", { name: "Bracket" }));
  await user.click(await screen.findByRole("button", { name: /Revision 1/ }));
}

function files(target = printer) {
  return (
    <PrinterFiles
      printer={target}
      files={[]}
      startingId={null}
      deletingId={null}
      syncing={false}
      onSync={() => {}}
      onStart={() => {}}
      onDelete={() => {}}
      onOpenJobs={() => {}}
    />
  );
}

describe("Upload from library", () => {
  it("confirms only after the fixed printer accepts the upload", async () => {
    const user = userEvent.setup();
    const response = Promise.withResolvers<Response>();
    const app = renderApp(files(), {
      seed: [
        [printerKeys.files(8), []],
        [printerKeys.jobs(8), []],
      ],
      routes: { ...routes(), "POST /api/v1/printers/8/send": () => response.promise },
    });
    await user.click(screen.getByRole("button", { name: "Upload from library" }));
    await choose(user);
    expect(app.requestsWithMethod("POST")).toHaveLength(0);

    await user.click(screen.getByRole("button", { name: "Upload file" }));
    expect(await screen.findByText("Uploading file to printer…")).toBeVisible();
    const posts = app.requestsWithMethod("POST");
    expect(posts).toHaveLength(1);
    expect(posts[0]?.url).toContain("/printers/8/send");
    expect(JSON.parse(posts[0]?.body ?? "{}")).toEqual({ file_id: 7, start_print: false });

    await act(async () => response.resolve(json(receipt)));
    expect(await screen.findByText("Uploaded bracket__saved.gcode")).toBeVisible();
    expect(app.client.getQueryState(printerKeys.files(8))?.isInvalidated).toBe(true);
    expect(app.client.getQueryState(printerKeys.jobs(8))?.isInvalidated).toBe(true);
  });

  it("requires an explicit plate for a multi-plate Revision", async () => {
    const user = userEvent.setup();
    const onUploaded = vi.fn<(name: string) => void>();
    const app = renderApp(
      <PrinterLibraryUpload printer={printer} onClose={() => {}} onUploaded={onUploaded} />,
      {
        routes: {
          ...routes("bracket.gcode.3mf"),
          "GET /api/v1/files/7/gcode-plates": json({ plates: [2, 10] }),
        },
      },
    );
    await choose(user);
    const button = screen.getByRole("button", { name: "Upload file" });
    const plate = await screen.findByRole("combobox", { name: "Sliced plate" });
    expect(button).toBeDisabled();

    await user.selectOptions(plate, "10");
    await user.click(button);
    await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(receipt.remote_filename));
    expect(JSON.parse(app.requestsWithMethod("POST")[0]?.body ?? "{}")).toEqual({
      file_id: 7,
      plate_index: 10,
      start_print: false,
    });
  });

  it("blocks a format the printer cannot accept", async () => {
    const user = userEvent.setup();
    renderApp(<PrinterLibraryUpload printer={printer} onClose={() => {}} onUploaded={() => {}} />, {
      routes: routes("bracket.bgcode"),
    });
    await choose(user);
    expect(
      await screen.findByText("This printer does not accept the selected file format."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Upload file" })).toBeDisabled();
  });

  it("keeps a failed transfer unconfirmed", async () => {
    const user = userEvent.setup();
    renderApp(files(), {
      routes: {
        ...routes(),
        "POST /api/v1/printers/8/send": json({ detail: "printer_unreachable" }, 502),
      },
    });
    await user.click(screen.getByRole("button", { name: "Upload from library" }));
    await choose(user);
    await user.click(screen.getByRole("button", { name: "Upload file" }));

    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByText("Uploaded bracket__saved.gcode")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload file" })).toBeEnabled();
  });

  it("offers upload on a provider without inventory", () => {
    renderApp(
      files(
        aPrinter({
          capabilities: printerCapabilities({ can_list_files: false, can_start: false }),
        }),
      ),
    );
    expect(screen.getByRole("button", { name: "Upload from library" })).toBeEnabled();
  });

  it("withholds upload from a printer viewer", () => {
    renderApp(files(aPrinter({ access: printerAccess({ role: "view", can_print: false }) })));
    expect(screen.queryByRole("button", { name: "Upload from library" })).not.toBeInTheDocument();
  });
});
