import { readFileSync } from "node:fs";
import { expect, test } from "@playwright/test";
import { collapseNavigationSidebar } from "../util";

interface PreparedPrinter {
  id: number;
  kind: string;
  lifecycle?: { jobId: number; fileId: number; filename: string };
}

const session: { token: string; user: object; printers: PreparedPrinter[] } = JSON.parse(
  readFileSync(process.env.FARM_BROWSER_SESSION!, "utf8"),
);

test.beforeEach(async ({ page, baseURL }) => {
  await page
    .context()
    .addCookies([
      { name: "printstash_session", value: session.token, url: baseURL!, httpOnly: true },
    ]);
  await page.addInitScript(
    (user) => localStorage.setItem("printstash.user", JSON.stringify(user)),
    session.user,
  );
});

for (const printer of session.printers) {
  const run = printer.lifecycle;
  if (!run) continue;
  if (printer.kind === "octoprint") {
    test("resumes an externally paused OctoPrint job from Status", async ({ page }) => {
      await page.goto(`/printers/${printer.id}`);
      await collapseNavigationSidebar(page);
      await page.getByRole("button", { name: "Resume", exact: true }).click();
      await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeEnabled();
      await expect
        .poll(async () => {
          const response = await page.request.get(`/api/v1/printers/${printer.id}/jobs`);
          const jobs: { id: number; state: string }[] = await response.json();
          return jobs.find((j) => j.id === run.jobId)?.state;
        })
        .toBe("printing");
    });
  } else {
    test("PrusaLink preserves a finished run after reload and a second start", async ({ page }) => {
      await page.goto(`/printers/${printer.id}?tab=jobs`);
      await collapseNavigationSidebar(page);
      const row = (id: number) =>
        page.getByRole("row").filter({ hasText: new RegExp(`Run #${id}(?![0-9])`) });
      await expect(row(run.jobId)).toContainText("Completed");
      const response = await page.request.post(`/api/v1/printers/${printer.id}/start`, {
        data: { file_id: run.fileId, remote_filename: run.filename },
      });
      expect(response.ok(), await response.text()).toBeTruthy();
      const next = await response.json();
      expect(next.id).not.toBe(run.jobId);
      await page.reload();
      await expect(row(next.id)).toBeVisible();
      await expect(row(run.jobId)).toContainText("Completed");
    });
  }
}
