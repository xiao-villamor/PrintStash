/**
 * Every site a marketplace import may contact, granted in one browser prompt.
 *
 * Chrome closes the toolbar popup while its permission prompt is open, and a
 * prompt shown after a network lookup can lose the click that allowed it. So
 * access is asked once, before anything is fetched: when the browser connects,
 * or at the first import click from a marketplace. Each list is the union of a
 * provider's page hosts and the download and gallery hosts its adapter already
 * accepts, so nothing discovered later needs another prompt.
 */
import { CULTS_DOWNLOAD_ORIGINS } from "./cults-capture.ts";
import { MAKERWORLD_ALLOWED_DOWNLOAD_HOSTS } from "./makerworld-capture.ts";
import { MYMINIFACTORY_DOWNLOAD_ORIGINS } from "./myminifactory-capture.ts";
import { SOURCE_IMAGE_HOSTS } from "./source-gallery.ts";
import { SOURCE_PROVIDERS, type SourceProviderCode } from "./source-providers.ts";
import { THINGIVERSE_PERMISSION_ORIGINS } from "./thingiverse-capture.ts";

const hostOrigin = (host: string) => `https://${host}/*`;

/** Download and API origins beyond the page and gallery hosts. */
const TRANSFER_ORIGINS: Record<SourceProviderCode, readonly string[]> = {
  makerworld: MAKERWORLD_ALLOWED_DOWNLOAD_HOSTS.map(hostOrigin),
  // Printables serves its API, files and media from printables.com subdomains,
  // and its adapter accepts download links from any of them.
  printables: ["https://*.printables.com/*"],
  thingiverse: THINGIVERSE_PERMISSION_ORIGINS,
  myminifactory: MYMINIFACTORY_DOWNLOAD_ORIGINS,
  cults: CULTS_DOWNLOAD_ORIGINS,
  thangs: [],
  crealitycloud: [],
  makeronline: ["https://acop-prod-private.s3-accelerate.amazonaws.com/*"],
};

export function marketplaceAccessOrigins(provider: SourceProviderCode): string[] {
  const page = SOURCE_PROVIDERS.find((entry) => entry.code === provider)?.hosts ?? [];
  return [
    ...new Set([
      ...page.map(hostOrigin),
      ...TRANSFER_ORIGINS[provider],
      ...SOURCE_IMAGE_HOSTS[provider].map(hostOrigin),
    ]),
  ];
}

export const ALL_MARKETPLACE_ACCESS_ORIGINS: readonly string[] = [
  ...new Set(SOURCE_PROVIDERS.flatMap((provider) => marketplaceAccessOrigins(provider.code))),
];
