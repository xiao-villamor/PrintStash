import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import { test, expect } from "@playwright/test";

// This dedicated provider suite seeds its actual API/S3 fixture before Chromium
// starts. Its dummy credential is installed as the app's real HttpOnly cookie.
test("downloads native S3 bytes through the authenticated frontend helper", async ({
  page,
  baseURL,
}) => {
  const dataRoot = process.env.PLAYWRIGHT_DELIVERY_DATA_DIR!;
  const manifest = JSON.parse(await readFile(resolve(dataRoot, "manifest.json"), "utf8"));
  const observationsPath = resolve(dataRoot, "responses.jsonl");
  const precedingResponses = (await readFile(observationsPath, "utf8"))
    .trim()
    .split("\n")
    .filter(Boolean).length;
  await page.context().addCookies([
    {
      name: "printstash_session",
      value: manifest.token,
      url: baseURL!,
      httpOnly: true,
      sameSite: "Strict",
    },
  ]);
  await page.goto("/tests/e2e-real/delivery/harness.html");

  const redirectResponse = page.waitForResponse(
    (response) => response.url() === `${baseURL}${manifest.path}` && response.status() === 307,
  );
  const providerResponse = page.waitForResponse(
    (response) =>
      response.url().startsWith(`${manifest.providerOrigin}/`) &&
      response.request().method() === "GET",
  );
  const downloaded = page.waitForEvent("download");
  await page.evaluate(async (path) => {
    const moduleUrl = "/src/lib/api/request.ts";
    const { downloadAuthenticatedFile } = await import(moduleUrl);
    await downloadAuthenticatedFile(path);
  }, manifest.path);

  const [redirect, provider, download] = await Promise.all([
    redirectResponse,
    providerResponse,
    downloaded,
  ]);
  expect(await redirect.headerValue("cache-control")).toBe("private, no-store");
  expect(await redirect.headerValue("referrer-policy")).toBe("no-referrer");
  expect(await redirect.headerValue("content-length")).toBe("0");
  expect((await redirect.request().allHeaders()).cookie).toContain("printstash_session=");
  expect(provider.status()).toBe(200);
  expect(await provider.headerValue("access-control-allow-origin")).toBe(baseURL);
  expect(await provider.headerValue("access-control-expose-headers")).toMatch(
    /content-disposition/i,
  );
  const providerHeaders = await provider.request().allHeaders();
  expect(providerHeaders.authorization).toBeUndefined();
  expect(providerHeaders.cookie).toBeUndefined();
  expect(providerHeaders.referer).toBeUndefined();
  expect(download.suggestedFilename()).toBe(manifest.filename);
  const saved = await download.path();
  expect(saved).not.toBeNull();
  expect(await readFile(saved!, "utf8")).toBe(manifest.payload);

  // Count the actual ASGI body messages for this browser request, independent
  // of Content-Length. A proxy retry would add a 200 row and fail this assertion.
  await expect(async () => {
    const rows = (await readFile(observationsPath, "utf8"))
      .trim()
      .split("\n")
      .filter(Boolean)
      .slice(precedingResponses)
      .map((line) => JSON.parse(line));
    expect(rows).toEqual([{ path: manifest.path, status: 307, bodyBytes: 0 }]);
  }).toPass();
});
