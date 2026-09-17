/** A real browser and backend preserve normalized filters without repeated chat calls. */
import { createServer } from "node:http";
import { test, expect } from "../helpers";
import { modelCard, uploadModel } from "../util";
import type { SearchSettingsRead } from "../../../src/types/search";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("AI Search", () => {
  test("persists editable filters without repeated parsing", async ({ page }, testInfo) => {
    const initial: SearchSettingsRead = await (
      await page.request.get(`${API}/api/v1/config/ai-search`)
    ).json();
    const personal = await (await page.request.get(`${API}/api/v1/search/preferences`)).json();
    const name = `NL bracket ${Date.now()}`;
    let modelId: number | undefined;
    let savedId: number | undefined;
    let calls = 0;
    let probe = true;
    const value = {
      residual_query: name,
      sort: "relevance",
      filters: {
        collection_id: null,
        printer_id: null,
        tag: [],
        material_type: [],
        printed: false,
        print_outcome: [],
        printed_period: null,
        printed_after: null,
        printed_before: null,
        print_duration_min_s: null,
        print_duration_max_s: null,
      },
    };
    // A real local HTTP ChatProvider stand-in, never a browser route interception.
    const server = createServer(async (request, response) => {
      let body = "";
      for await (const chunk of request) body += chunk;
      const payload = JSON.parse(body);
      if (
        request.url !== "/v1/chat/completions" ||
        payload.response_format?.type !== "json_schema" ||
        payload.stream !== false
      ) {
        response.writeHead(422).end();
        return;
      }
      const result = probe ? { probe: "ok" } : value;
      if (!probe) calls += 1;
      response.setHeader("Content-Type", "application/json");
      response.end(
        JSON.stringify({
          choices: [
            {
              finish_reason: "stop",
              message: { role: "assistant", content: JSON.stringify(result) },
            },
          ],
        }),
      );
    });
    const chatPort = Number(
      process.env.PLAYWRIGHT_NL_CHAT_PORT ?? Number(new URL(API).port) + 1000,
    );
    await new Promise<void>((resolve) => server.listen(chatPort, "127.0.0.1", resolve));
    try {
      await uploadModel(page, name, { mesh: true, gcode: false });
      const href = await modelCard(page, name).getAttribute("href");
      modelId = Number(href?.split("/").at(-1));
      const immediate = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return url.pathname === "/api/v1/search" && url.searchParams.get("instant") === "true";
      });
      await page.getByRole("searchbox", { name: "Search library" }).fill(name);
      await expect(
        page.locator("[data-search-suggestion]").filter({ hasText: name }),
      ).toBeVisible();
      const instant = await (await immediate).json();
      expect(instant.legs).toEqual(["lexical"]);
      expect(instant.generations).toEqual([]);
      const endpoint = await page.request.post(`${API}/api/v1/config/ai-search/endpoints`, {
        data: {
          base_url: `http://127.0.0.1:${chatPort}/v1`,
          model: "local-parser-fixture",
          kind: "chat",
        },
      });
      expect(endpoint.status()).toBe(201);
      probe = false;
      const configured = await page.request.put(`${API}/api/v1/config/ai-search`, {
        data: {
          ...initial.settings,
          enabled: true,
          chat_endpoint_id: (await endpoint.json()).id,
          nl_filters_enabled: true,
          timezone: "Europe/Madrid",
        },
      });
      expect(configured.ok()).toBe(true);
      await page.goto("/search");
      await page.getByText("Search options", { exact: true }).click();
      await page.getByRole("button", { name: "Natural-language search", exact: true }).click();
      await expect(page.getByRole("dialog")).toContainText(
        "submitted searches and available filter choices are sent to 127.0.0.1",
      );
      await page
        .getByRole("checkbox", { name: "Interpret my searches as editable filters" })
        .check();
      await page.getByRole("button", { name: "Save preferences" }).click();
      const input = page.getByRole("searchbox");
      await input.fill(`${name} never printed`);
      await input.press("Enter");
      const chip = page.getByRole("button", { name: /^Remove Has print history/ });
      await expect(chip).toBeVisible();
      await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
      expect(page.url()).toContain("printed=no");
      expect(page.url()).not.toContain("parse=1");
      expect(calls).toBe(1);
      // Dates/durations can also be added explicitly after parsing.
      await page.getByRole("button", { name: "Print history filters", exact: true }).click();
      await page.getByRole("spinbutton", { name: "Actual duration < seconds" }).fill("10800");
      await page.getByRole("button", { name: "Apply filters", exact: true }).click();
      await expect(
        page.getByRole("button", { name: "Remove Actual duration < seconds: 10800" }),
      ).toBeVisible();
      await page.getByRole("button", { name: /^Saved views(?: \d+)?$/ }).click();
      await page.getByRole("button", { name: "Save current view", exact: true }).click();
      await page.getByRole("textbox", { name: "View name" }).fill(name);
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/saved-views") && response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Save view", exact: true }).click();
      const row = await (await saved).json();
      savedId = row.id;
      expect(row.filters).toMatchObject({ q: name, printed: false, print_duration_max_s: 10800 });
      expect(calls).toBe(1);
      for (const [label, width, height] of [
        ["desktop", 1280, 900],
        ["mobile", 390, 844],
      ] as const) {
        await page.setViewportSize({ width, height });
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(
          true,
        );
        await page.screenshot({
          path: testInfo.outputPath(`nl-filters-${label}.png`),
          fullPage: true,
        });
      }
      await page.getByRole("button", { name: "Remove Actual duration < seconds: 10800" }).click();
      await expect(page.getByRole("link", { name, exact: true })).toBeVisible();
      expect(calls).toBe(1);
      // A persisted normalized view restores without another language-model call.
      await page.goto("/search");
      await page.getByText("Search options", { exact: true }).click();
      await page.getByRole("button", { name: /^Saved views(?: \d+)?$/ }).click();
      await page.getByRole("button", { name, exact: true }).click();
      await expect(
        page.getByRole("button", { name: "Remove Actual duration < seconds: 10800" }),
      ).toBeVisible();
      expect(calls).toBe(1);
    } finally {
      await page.request.put(`${API}/api/v1/config/ai-search`, { data: initial.settings });
      await page.request.patch(`${API}/api/v1/search/preferences`, {
        data: { nl_filters_enabled: personal.nl_filters_enabled, timezone: personal.timezone },
      });
      if (savedId) await page.request.delete(`${API}/api/v1/saved-views/${savedId}`);
      if (modelId) await page.request.delete(`${API}/api/v1/models/${modelId}`);
      await new Promise<void>((resolve, reject) =>
        server.close((error) => (error ? reject(error) : resolve())),
      );
    }
  });
});
