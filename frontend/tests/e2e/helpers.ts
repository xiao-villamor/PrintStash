import { expect, test, type Locator, type Page } from "@playwright/test";
import type { Server } from "node:http";

import { resetMockApiState, setExternalLibrariesEnabled, startMockApi } from "./mock-api";

const apiPort = Number(process.env.PLAYWRIGHT_API_PORT ?? 4210);

export { expect, setExternalLibrariesEnabled, test, type Locator, type Page };

export function installMockApiHooks(): void {
  let api: Server;

  test.beforeAll(async () => {
    api = await startMockApi(apiPort);
  });

  test.afterAll(async () => {
    await new Promise<void>((resolve, reject) => {
      api.close((error) => (error ? reject(error) : resolve()));
    });
  });

  test.beforeEach(async ({ page }) => {
    resetMockApiState();
    await page.addInitScript(() => {
      localStorage.setItem("printstash.token", "test-token");
      localStorage.setItem(
        "printstash.user",
        JSON.stringify({
          id: 1,
          username: "tester",
          email: null,
          is_superuser: true,
        }),
      );
    });
  });
}

export async function collectPageProblems(page: Page): Promise<string[]> {
  const problems: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      if (message.text().includes("/api/v1/printers/3/ws")) return;
      problems.push(`console error: ${message.text()}`);
    }
  });
  page.on("pageerror", (error) => {
    problems.push(`page error: ${error.message}`);
  });
  page.on("requestfailed", (request) => {
    const url = request.url();
    if (url.includes("_rsc=")) return;
    problems.push(`request failed: ${url} ${request.failure()?.errorText ?? ""}`);
  });
  page.on("response", (response) => {
    const url = response.url();
    if (response.status() >= 400 && !url.includes("_rsc=")) {
      problems.push(`bad response: ${response.status()} ${url}`);
    }
  });
  return problems;
}
