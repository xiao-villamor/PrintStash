/** One prompt must cover every host an import can contact, or a second prompt follows. */
import { describe, expect, it } from "vitest";
import { CULTS_DOWNLOAD_ORIGINS } from "../cults-capture.ts";
import { MAKERWORLD_ALLOWED_DOWNLOAD_HOSTS } from "../makerworld-capture.ts";
import { ALL_MARKETPLACE_ACCESS_ORIGINS, marketplaceAccessOrigins } from "../marketplace-access.ts";
import { MYMINIFACTORY_DOWNLOAD_ORIGINS } from "../myminifactory-capture.ts";
import { PRINTABLES_METADATA_PERMISSION_ORIGIN } from "../printables-capture.ts";
import { SOURCE_IMAGE_HOSTS } from "../source-gallery.ts";
import { MARKETPLACE_ORIGINS, SOURCE_PROVIDERS } from "../source-providers.ts";
import { THINGIVERSE_PERMISSION_ORIGINS } from "../thingiverse-capture.ts";

/** Chrome's match-pattern rule for an `https://host/*` or `https://*.host/*` grant. */
function covers(origins: readonly string[], url: string) {
  const { hostname } = new URL(url);
  return origins.some((origin) => {
    const host = new URL(origin.replace("*.", "wildcard.").replace(/\*$/, "")).hostname;
    return origin.includes("://*.")
      ? hostname === host.replace(/^wildcard\./, "") ||
          hostname.endsWith(host.replace(/^wildcard/, ""))
      : hostname === host;
  });
}

describe("marketplace access", () => {
  it.each(SOURCE_PROVIDERS.map((provider) => provider.code))(
    "covers every %s page and gallery host",
    (provider) => {
      const origins = marketplaceAccessOrigins(provider);
      const page = SOURCE_PROVIDERS.find((entry) => entry.code === provider)?.hosts ?? [];
      for (const host of [...page, ...SOURCE_IMAGE_HOSTS[provider]])
        expect(covers(origins, `https://${host}/`), host).toBe(true);
    },
  );

  it("covers every download host the adapters accept", () => {
    const expected: Array<[Parameters<typeof marketplaceAccessOrigins>[0], string[]]> = [
      ["makerworld", MAKERWORLD_ALLOWED_DOWNLOAD_HOSTS.map((host) => `https://${host}/`)],
      [
        "printables",
        [
          PRINTABLES_METADATA_PERMISSION_ORIGIN,
          "https://files.printables.com/",
          "https://media.printables.com/",
        ],
      ],
      ["thingiverse", THINGIVERSE_PERMISSION_ORIGINS],
      ["cults", CULTS_DOWNLOAD_ORIGINS],
      ["myminifactory", MYMINIFACTORY_DOWNLOAD_ORIGINS],
      ["makeronline", ["https://acop-prod-private.s3-accelerate.amazonaws.com/"]],
    ];
    for (const [provider, urls] of expected)
      for (const url of urls)
        expect(covers(marketplaceAccessOrigins(provider), url.replace(/\*$/, "")), url).toBe(true);
  });

  it("asks only for HTTPS sites, each once", () => {
    expect(new Set(ALL_MARKETPLACE_ACCESS_ORIGINS).size).toBe(
      ALL_MARKETPLACE_ACCESS_ORIGINS.length,
    );
    for (const origin of ALL_MARKETPLACE_ACCESS_ORIGINS)
      expect(origin).toMatch(/^https:\/\/.+\/\*$/);
  });

  it("includes the page origins the launcher registers on", () => {
    for (const origin of MARKETPLACE_ORIGINS)
      expect(ALL_MARKETPLACE_ACCESS_ORIGINS).toContain(origin);
  });
});
