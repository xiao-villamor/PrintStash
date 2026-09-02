/* Opt-in checks against the real provider pages used in the bug report. */

import assert from "node:assert/strict";

import {
  MAKERWORLD_METADATA_FIXTURE_VERSION,
  MAKERWORLD_MAX_RESPONSE_BYTES,
  requestMakerWorldLinksInMainWorld,
  requestMakerWorldMetadataInMainWorld,
} from "../../makerworld-capture";

interface LiveProviderBrowser {
  execute<Result, Argument>(
    script: (argument: Argument) => Result,
    argument: Argument,
  ): Promise<Result>;
  url(destination: string): Promise<void>;
}

declare const browser: LiveProviderBrowser;

const describeLive = process.env.PRINTSTASH_LIVE_PROVIDER_E2E === "1" ? describe : describe.skip;
const itMakerWorldDownload =
  process.env.PRINTSTASH_MAKERWORLD_REQUIRE_DOWNLOAD === "1" ? it : it.skip;
const makerWorldUrl =
  "https://makerworld.com/en/models/1574312-design-headphone-stand-swing-quickprint?from=recommend#profileId-1656140";
const thingiverseUrl = "https://www.thingiverse.com/thing:7401604/files";

describeLive("live provider capture contracts", () => {
  it("reads the supplied MakerWorld package list in Chromium", async () => {
    await browser.url(makerWorldUrl);
    const result = await browser.execute(requestMakerWorldMetadataInMainWorld, {
      endpoint: "https://makerworld.com/api/v1/design-service/design/1574312",
      sourceItemId: "1574312",
      fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
      maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
    });

    assert.equal(result.ok, true, `MakerWorld metadata failed with ${result.code ?? "no code"}`);
    assert.ok(result.metadata?.files.some((file) => file.id === "1656140"));
  });

  itMakerWorldDownload(
    "resolves the supplied MakerWorld package in a signed-in Chromium profile",
    async () => {
      await browser.url(makerWorldUrl);
      const result = await browser.execute(requestMakerWorldLinksInMainWorld, {
        endpoint: "https://makerworld.com/api/v1/design-service/instance",
        selectedIds: ["1656140"],
        maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
      });

      assert.equal(
        result.ok,
        true,
        `MakerWorld link resolution failed with ${result.code ?? "no code"}`,
      );
      assert.equal(result.links?.[0]?.id, "1656140");
    },
  );

  it("streams the supplied Thingiverse ZIP route in Chromium", async () => {
    await browser.url(thingiverseUrl);
    const result = await browser.execute(async (url) => {
      try {
        const response = await fetch(url, { credentials: "include", cache: "no-store" });
        const contentType = response.headers.get("Content-Type") || "";
        if (!response.ok || contentType.toLowerCase().includes("text/html") || !response.body) {
          return { ok: false, status: response.status, contentType, finalUrl: response.url };
        }
        const reader = response.body.getReader();
        const first = await reader.read();
        await reader.cancel();
        return {
          ok: !first.done && first.value[0] === 0x50 && first.value[1] === 0x4b,
          status: response.status,
          contentType,
          finalUrl: response.url,
        };
      } catch {
        return { ok: false, status: 0, contentType: "", finalUrl: "" };
      }
    }, "https://www.thingiverse.com/thing:7401604/zip");

    assert.equal(
      result.ok,
      true,
      `Thingiverse ZIP failed with HTTP ${result.status} (${result.contentType})`,
    );
    assert.match(new URL(result.finalUrl).hostname, /^(?:www\.|cdn\.|api\.)?thingiverse\.com$/);
  });
});
