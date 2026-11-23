/** An operator transfers an exact Revision from Fleet to each native printer and reopens its history. */
import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import type { PrintJobRead } from "../../../src/types";
import { collapseNavigationSidebar } from "../util";

interface FarmPrinter {
  id: number;
  kind: string;
  name: string;
  fileId: number;
  travelX: number | null;
  revisionName: string;
  canList: boolean;
}

const path = process.env.FARM_BROWSER_SESSION;
if (!path)
  throw new Error("Run deploy/manual-testing/bin/farm-lab.sh to create a disposable farm.");
const session: { token: string; user: object; printers: FarmPrinter[] } = JSON.parse(
  readFileSync(path, "utf8"),
);

test.describe("Native printer operator workflow", () => {
  for (const printer of session.printers) {
    test(`${printer.kind} uploads a saved Revision from Files`, async ({ page, baseURL }) => {
      if (!baseURL) throw new Error("Missing disposable Farm Lab URL");
      await page.context().addCookies([
        {
          name: "printstash_session",
          value: session.token,
          url: baseURL,
          httpOnly: true,
          sameSite: "Strict",
        },
      ]);
      await page.addInitScript(
        (user) => localStorage.setItem("printstash.user", JSON.stringify(user)),
        session.user,
      );
      await page.goto("/printers");
      await collapseNavigationSidebar(page);
      await page.getByRole("link", { name: printer.name, exact: true }).click();
      await expect(page.getByRole("heading", { name: printer.name, exact: true })).toBeVisible();
      if (printer.travelX !== null) {
        await page.getByRole("tab", { name: "Config", exact: true }).click();
        await page.getByRole("tab", { name: "Reported configuration" }).click();
        const reported = page.getByRole("tabpanel", { name: "Reported configuration" });
        await reported.getByRole("searchbox").fill("stepper_x");
        await reported.getByRole("button", { name: "stepper_x", exact: true }).first().click();
        const axis = reported.getByRole("region", { name: "stepper_x", exact: true });
        await expect(axis.getByText("position_max", { exact: true })).toBeVisible();
        await expect(axis.getByText(String(printer.travelX), { exact: true })).toBeVisible();
      }
      if (printer.kind === "bambu") {
        await expect(page.getByRole("region", { name: "AMS 0" }).getByText("PETG")).toBeVisible();
      }
      await page.getByRole("tab", { name: "Files", exact: true }).click();
      await page.getByRole("button", { name: "Upload from library" }).click();
      const dialog = page.getByRole("dialog", { name: "Upload from library" });
      await dialog.getByRole("button", { name: "Revision" }).click();
      const picker = page.getByRole("dialog", { name: "Revision", exact: true });
      await picker.getByRole("button", { name: printer.revisionName, exact: true }).click();
      await picker.getByRole("button", { name: /Revision 1/ }).click();
      const sent = page.waitForResponse(
        (r) => r.url().endsWith(`/printers/${printer.id}/send`) && r.request().method() === "POST",
      );
      await dialog.getByRole("button", { name: "Upload file", exact: true }).click();
      const response = await sent;
      expect(response.status(), await response.text()).toBe(200);
      expect(response.request().postDataJSON()).toEqual({
        file_id: printer.fileId,
        start_print: false,
      });
      const receipt: PrintJobRead = await response.json();
      await expect(page.getByText(`Uploaded ${receipt.remote_filename}`)).toBeVisible();
      if (printer.canList) {
        await page.getByRole("button", { name: "Inspect file" }).click();
        await expect(page.getByRole("complementary", { name: "Selected file" })).toContainText(
          receipt.remote_filename,
        );
        await page.getByRole("tab", { name: "Jobs", exact: true }).click();
      } else {
        await expect(page.getByText("Files you send appear in Jobs.")).toBeVisible();
        await page.getByRole("button", { name: "Open in Jobs" }).click();
      }
      await page.reload();
      await expect(page.getByText("No print start recorded").first()).toBeVisible();
      const history: PrintJobRead[] = await (
        await page.request.get(`/api/v1/printers/${printer.id}/jobs`)
      ).json();
      expect(history).toEqual([
        expect.objectContaining({
          id: receipt.id,
          file_id: printer.fileId,
          started_at: null,
          state: "completed",
        }),
      ]);
    });
  }
});
