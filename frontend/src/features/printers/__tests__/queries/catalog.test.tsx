/** Printer catalog acknowledgements update the shared choices and dashboard. */
import { useState } from "react";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { usePrinterCatalogCommands } from "../../queries";
import { usePrinters } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { json, renderApp } from "@/test-support/render";
import { aPrinter } from "@/test-support/factories";
function Catalog({ remove }: { remove: boolean }) {
  const commands = usePrinterCatalogCommands();
  const read = usePrinters();
  const [result, setResult] = useState("idle");
  return (
    <>
      <p>{read.data?.map((printer) => printer.name).join(",") || "empty"}</p>
      <p>{result}</p>
      <button
        onClick={() => {
          void (
            remove ? commands.deletePrinter(7) : commands.createPrinter({ name: "New printer" })
          ).then(
            () => setResult("saved"),
            () => setResult("failed"),
          );
        }}
      >
        Save
      </button>
    </>
  );
}
describe("printer catalog commands", () => {
  it.each([false, true])("refreshes choices after confirmed removal=%s", async (remove) => {
    let completed = false;
    const row = aPrinter({ id: 7, name: "New printer" });
    const app = renderApp(<Catalog remove={remove} />, {
      seed: [[queryKeys.printerDashboard, {}]],
      routes: {
        "GET /api/v1/printers": () => json(completed !== remove ? [row] : []),
        "POST /api/v1/printers": () => {
          completed = true;
          return json(row);
        },
        "DELETE /api/v1/printers/7": () => {
          completed = true;
          return json(null, 204);
        },
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("saved");
    expect(await screen.findByText(remove ? "empty" : "New printer")).toBeVisible();
    expect(app.client.getQueryState(queryKeys.printerDashboard)?.isInvalidated).toBe(true);
  });
});
