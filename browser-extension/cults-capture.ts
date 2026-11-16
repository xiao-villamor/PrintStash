import {
  buildBrowserCaptureMessage,
  type BrowserCaptureMessage,
  type BrowserSourceMetadata,
} from "./capture-adapter.ts";
import { MarketplaceDownloadError, downloadMarketplaceFile } from "./marketplace-download.ts";

export const CULTS_DOWNLOAD_ORIGINS = ["https://cults3d.com/*", "https://download.cults3d.com/*"];
export type CultsPageResult =
  | {
      ok: true;
      publicationId: string;
      source: BrowserSourceMetadata;
      jsonLd: string[];
      downloadUrl: string | null;
    }
  | { ok: false; code: "contract_changed" | "native_download_required" | "request_failed" };

/** Native free-order CSRF stays in the source page; no checkout or payment is performed. */
export async function requestCultsInMainWorld(args: {
  publicationId: string;
  operation: "metadata" | "download";
}): Promise<CultsPageResult> {
  const invalid = { ok: false, code: "contract_changed" } as const;
  if (
    location.hostname !== "cults3d.com" ||
    !/^[a-z0-9][a-z0-9-]{0,200}$/i.test(args.publicationId)
  )
    return invalid;
  const match = location.pathname.match(/^\/([a-z]{2})\/3d-model\/[^/]+\/([^/]+)\/?$/);
  if (!match || match[2] !== args.publicationId) return invalid;
  const pagePath = location.pathname;
  const locale = match[1];
  const forms = [...document.querySelectorAll<HTMLFormElement>("main form")];
  const form = forms.find((f) => {
    const url = new URL(f.getAttribute("action") || "", location.origin);
    return (
      f.method.toLowerCase() === "post" &&
      url.origin === location.origin &&
      url.pathname === `/${locale}/free_orders` &&
      url.searchParams.get("creation_slug") === args.publicationId &&
      [...url.searchParams.keys()].length === 1 &&
      !url.hash &&
      !url.username &&
      !url.password
    );
  });
  if (!form) return { ok: false, code: "native_download_required" };
  const source: BrowserSourceMetadata = {};
  const author = document.querySelector<HTMLAnchorElement>(`main h2 a[href^="/${locale}/users/"]`);
  if (author?.textContent?.trim() && author.textContent.trim().length <= 512) {
    const url = new URL(author.getAttribute("href")!, location.origin);
    if (
      url.origin === location.origin &&
      /^\/[a-z]{2}\/users\/[^/]+$/.test(url.pathname) &&
      !url.search &&
      !url.hash
    ) {
      source.creatorName = author.textContent.trim();
      source.creatorUrl = url.href;
    }
  }
  const license = document.querySelector<HTMLAnchorElement>(`main a[href^="/${locale}/licenses#"]`);
  if (license?.textContent?.trim() && license.textContent.trim().length <= 1024) {
    const terms = license.closest("ul")?.cloneNode(true) as HTMLElement | undefined;
    terms?.querySelectorAll("svg").forEach((node) => node.remove());
    const text = (
      terms
        ? [...terms.querySelectorAll("li")].map((node) => node.textContent?.trim()).join("; ")
        : license.textContent
    )
      .replace(/\s+/g, " ")
      .trim();
    if (text.length <= 1024) source.licenseText = text;
    source.licenseUrl = new URL(license.getAttribute("href")!, location.origin).href;
  }
  const scripts = [...document.querySelectorAll('script[type="application/ld+json"]')];
  if (scripts.length > 16) return invalid;
  const jsonLd = scripts.map((s) => s.textContent || "");
  if (
    jsonLd.some((s) => s.length > 256 * 1024) ||
    jsonLd.reduce((n, s) => n + s.length, 0) > 1024 * 1024
  )
    return invalid;
  const result = {
    ok: true,
    publicationId: args.publicationId,
    source,
    jsonLd,
    downloadUrl: null,
  } as const;
  if (args.operation === "metadata") return result;
  const csrf = form.querySelector<HTMLInputElement>('input[name="authenticity_token"]')?.value;
  if (!csrf || csrf.length > 4096) return invalid;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const response = await fetch(new URL(form.getAttribute("action")!, location.origin).href, {
      method: "POST",
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
      body: new URLSearchParams({ authenticity_token: csrf }),
    });
    const final = new URL(response.url);
    if (
      !response.ok ||
      final.origin !== location.origin ||
      !new RegExp(`^/${locale}/orders/\\d+$`).test(final.pathname) ||
      !response.body
    ) {
      await response.body?.cancel();
      return { ok: false, code: "native_download_required" };
    }
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let html = "",
      bytes = 0;
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        bytes += part.value.byteLength;
        if (bytes > 2 * 1024 * 1024) {
          await reader.cancel();
          return invalid;
        }
        html += decoder.decode(part.value, { stream: true });
      }
      html += decoder.decode();
    } finally {
      reader.releaseLock();
    }
    if (location.pathname !== pagePath) return invalid;
    const doc = new DOMParser().parseFromString(html, "text/html");
    const publication = doc.querySelector<HTMLAnchorElement>(
      'main a[data-turbo-target][href*="/3d-model/"]',
    );
    if (
      !publication ||
      new URL(publication.getAttribute("href")!, location.origin).pathname !== pagePath
    )
      return invalid;
    const links = [...doc.querySelectorAll<HTMLAnchorElement>('main a[data-turbo="false"]')]
      .map((a) => new URL(a.getAttribute("href") || "", location.origin))
      .filter(
        (u) =>
          u.origin === location.origin &&
          new RegExp(`^/${locale}/downloads/\\d+$`).test(u.pathname) &&
          (!u.search ||
            (u.searchParams.get("creation") === args.publicationId &&
              [...u.searchParams.keys()].length === 1)) &&
          !u.hash &&
          !u.username &&
          !u.password,
      );
    if (links.length !== 1) return invalid;
    const file = await fetch(links[0].href, {
      credentials: "same-origin",
      cache: "no-store",
      signal: controller.signal,
    });
    await file.body?.cancel();
    const url = new URL(file.url);
    if (
      !file.ok ||
      location.pathname !== pagePath ||
      url.protocol !== "https:" ||
      url.hostname !== "download.cults3d.com" ||
      url.port ||
      url.username ||
      url.password ||
      url.hash ||
      !/^\/uploads\/creation_zip\/file\/\d+\/[^/]+\.zip$/.test(url.pathname)
    )
      return invalid;
    return { ...result, downloadUrl: url.href };
  } catch {
    return { ok: false, code: "request_failed" };
  } finally {
    clearTimeout(timer);
  }
}
export function cultsCaptureFromPage(
  pageUrl: string,
  result: CultsPageResult,
): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "Cults",
    pageUrl,
    jsonLd: result.ok ? result.jsonLd : [],
    sourceMetadata: result.ok ? result.source : undefined,
  });
  if (!result.ok) return capture;
  return {
    ...capture,
    state: "ready",
    candidates: [
      {
        id: `cults:${result.publicationId}:archive`,
        filename: `${result.publicationId}.zip`,
        fileType: "other",
      },
    ],
  };
}
export function isCultsArchiveUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname === "download.cults3d.com" &&
      !url.port &&
      !url.username &&
      !url.password &&
      !url.hash &&
      /^\/uploads\/creation_zip\/file\/\d+\/[^/]+\.zip$/.test(url.pathname)
    );
  } catch {
    return false;
  }
}
export async function downloadCultsCandidate(options: {
  candidate: BrowserCaptureMessage["candidates"][number];
  result: CultsPageResult;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}) {
  const { candidate, result, signal } = options;
  if (
    !result.ok ||
    candidate.id !== `cults:${result.publicationId}:archive` ||
    candidate.filename !== `${result.publicationId}.zip` ||
    !result.downloadUrl
  )
    throw new MarketplaceDownloadError(
      "Cults download is not available. Open the native download on Cults, then retry. Paid purchases must be completed on the source site.",
    );
  if (!isCultsArchiveUrl(result.downloadUrl))
    throw new MarketplaceDownloadError(
      "Cults did not return a supported archive. Download it on Cults and attach it in Pending Imports.",
    );
  return downloadMarketplaceFile({
    candidate,
    url: result.downloadUrl,
    provider: "Cults",
    allowedHosts: ["download.cults3d.com"],
    ensureOriginPermission: async () => {},
    signal,
    fetchImpl: options.fetchImpl,
  });
}
