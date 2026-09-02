/* Opt-in checks against the real provider pages used in the bug report. */

import assert from "node:assert/strict";

import {
  MAKERWORLD_METADATA_FIXTURE_VERSION,
  MAKERWORLD_MAX_RESPONSE_BYTES,
  requestMakerWorldLinksInMainWorld,
  requestMakerWorldMetadataInMainWorld,
} from "../../makerworld-capture";
import {
  THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
  requestThingiverseFilesInMainWorld,
} from "../../thingiverse-capture";

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

  it("discovers individual files on the supplied Thingiverse page in Chromium", async () => {
    await browser.url(thingiverseUrl);
    const result = await browser.execute(requestThingiverseFilesInMainWorld, {
      sourceItemId: "7401604",
      endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
      maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
    });

    assert.equal(result.ok, true, `Thingiverse file discovery failed with ${result.code}`);
    assert.ok(result.files?.length, "Thingiverse returned no individual file candidates");
    for (const file of result.files ?? []) {
      assert.match(new URL(file.url).hostname, /^(?:www\.|cdn\.|api\.)?thingiverse\.com$/);
      assert.doesNotMatch(file.url, /\/zip(?:[/?]|$)/);
    }
  });
});
