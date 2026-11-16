import type { ByteProgress } from "./transfer-progress.ts";
import {
  buildBrowserCaptureMessage,
  type BrowserCaptureMessage,
  type BrowserSourceMetadata,
} from "./capture-adapter.ts";
import { MarketplaceDownloadError, readMarketplaceFile } from "./marketplace-download.ts";

export const MYMINIFACTORY_DOWNLOAD_ORIGINS = [
  "https://www.myminifactory.com/*",
  "https://dl4.myminifactory.com/*",
];
export type MyMiniFactoryPageResult =
  | {
      ok: true;
      publicationId: string;
      loggedIn: boolean;
      files: Array<{ id: string; filename: string }>;
      jsonLd: string[];
      source: BrowserSourceMetadata;
    }
  | { ok: false; code: "contract_changed" };

/** Reads explicit native file forms; unrelated account/CSRF properties are never returned. */
export function readMyMiniFactoryInMainWorld(args: {
  publicationId: string;
}): MyMiniFactoryPageResult {
  const invalid = { ok: false, code: "contract_changed" } as const;
  if (
    location.hostname !== "www.myminifactory.com" ||
    location.pathname !== `/object/${args.publicationId}`
  )
    return invalid;
  const id = args.publicationId.match(/(?:^|-)(\d{1,20})$/)?.[1];
  if (!id) return invalid;
  try {
    const node = document.querySelector(
      'script[type="application/json"][data-component-name="DownloadButton"]',
    );
    const raw = node?.textContent;
    if (!raw || raw.length > 256 * 1024) return invalid;
    const props: unknown = JSON.parse(raw);
    if (
      !props ||
      typeof props !== "object" ||
      !("download_url" in props) ||
      !("isLoggedIn" in props) ||
      typeof props.download_url !== "string" ||
      typeof props.isLoggedIn !== "boolean"
    )
      return invalid;
    const download = new URL(props.download_url, location.origin);
    if (
      download.origin !== location.origin ||
      download.pathname !== `/download/${id}` ||
      download.search ||
      download.hash ||
      download.username ||
      download.password
    )
      return invalid;
    const buttons = [
      ...document.querySelectorAll<HTMLButtonElement>('button[name="downloadfile"][data-file]'),
    ];
    if (!buttons.length || buttons.length > 64) return invalid;
    const files: Array<{ id: string; filename: string }> = [];
    for (const button of buttons) {
      const form = button.closest("form"),
        fileId = button.dataset.file,
        filename = button.value;
      if (
        !form ||
        form.method.toLowerCase() !== "get" ||
        !fileId ||
        !/^\d{1,20}$/.test(fileId) ||
        !filename ||
        filename.length > 240 ||
        /[\\/\p{Cc}<>]/u.test(filename) ||
        filename.startsWith(".") ||
        !/\.(?:stl|3mf|obj|step|stp|zip)$/i.test(filename)
      )
        return invalid;
      const action = new URL(form.action, location.origin);
      if (action.href !== download.href) return invalid;
      files.push({ id: fileId, filename });
    }
    if (new Set(files.map((f) => f.id)).size !== files.length) return invalid;
    const scripts = [...document.querySelectorAll('script[type="application/ld+json"]')];
    if (scripts.length > 16) return invalid;
    const jsonLd: string[] = [];
    let total = 0;
    for (const script of scripts) {
      const text = script.textContent;
      if (text === null || text.length > 256 * 1024) return invalid;
      total += text.length;
      if (total > 1024 * 1024) return invalid;
      jsonLd.push(text);
    }
    const source: BrowserSourceMetadata = {};
    const author = document.querySelector<HTMLAnchorElement>(
      '.user-download-container .designerWordFlex .txt-obj-medium a[href^="/users/"]',
    );
    const authorName = author?.textContent?.trim();
    if (authorName && authorName.length <= 512 && !/[\p{Cc}<>]/u.test(authorName)) {
      source.creatorName = authorName;
      const authorUrl = new URL(author!.getAttribute("href")!, location.origin);
      if (
        authorUrl.origin === location.origin &&
        /^\/users\/[^/]+$/.test(authorUrl.pathname) &&
        !authorUrl.search &&
        !authorUrl.hash
      )
        source.creatorUrl = authorUrl.href;
    }
    const license = document.querySelector<HTMLAnchorElement>(
      'table.table-spec a[href="https://www.myminifactory.com/object-licensing"]',
    );
    const licenseText = license?.textContent?.trim();
    if (licenseText && licenseText.length <= 1024 && !/[\p{Cc}<>]/u.test(licenseText)) {
      source.licenseText = licenseText;
      source.licenseUrl = "https://www.myminifactory.com/object-licensing";
    }
    return {
      ok: true,
      source,
      publicationId: args.publicationId,
      loggedIn: props.isLoggedIn,
      files,
      jsonLd,
    };
  } catch {
    return invalid;
  }
}
export function myMiniFactoryCaptureFromPage(
  pageUrl: string,
  result: MyMiniFactoryPageResult,
): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "MyMiniFactory",
    pageUrl,
    jsonLd: result.ok ? result.jsonLd : [],
    sourceMetadata: result.ok ? result.source : undefined,
  });
  if (!result.ok) return capture;
  return {
    ...capture,
    state: "ready",
    candidates: result.files.map((f) => ({
      id: f.id,
      filename: f.filename,
      fileType: /\.stl$/i.test(f.filename) ? "stl" : "other",
    })),
  };
}
export async function downloadMyMiniFactoryCandidate(options: {
  candidate: BrowserCaptureMessage["candidates"][number];
  result: MyMiniFactoryPageResult;
  ensureOriginPermissions(origins: string[]): Promise<void>;
  signal?: AbortSignal;
  progress?: ByteProgress;
  fetchImpl?: typeof fetch;
}) {
  const { candidate, result, signal } = options;
  if (
    !result.ok ||
    !result.files.some((f) => f.id === candidate.id && f.filename === candidate.filename)
  )
    throw new MarketplaceDownloadError(
      "MyMiniFactory file selection changed. Reopen the publication and select its files again.",
    );
  if (!result.loggedIn)
    throw new MarketplaceDownloadError("Sign in on MyMiniFactory before downloading, then retry.");
  const id = result.publicationId.match(/(?:^|-)(\d{1,20})$/)?.[1];
  if (!id) throw new MarketplaceDownloadError("MyMiniFactory publication identity changed.");
  await options.ensureOriginPermissions(MYMINIFACTORY_DOWNLOAD_ORIGINS);
  signal?.throwIfAborted();
  const url = new URL(`/download/${id}`, "https://www.myminifactory.com");
  url.searchParams.set("downloadfile", candidate.filename);
  const response = await (options.fetchImpl ?? fetch)(url.href, {
    credentials: "include",
    cache: "no-store",
    signal,
  });
  let safe = false;
  try {
    const final = new URL(response.url);
    safe =
      final.protocol === "https:" &&
      final.hostname === "dl4.myminifactory.com" &&
      !final.username &&
      !final.password &&
      !final.port &&
      !final.hash &&
      final.pathname.startsWith("/object-assets/") &&
      /\.(?:stl|3mf|obj|step|stp|zip)$/i.test(final.pathname);
  } catch {
    /* Missing/invalid redirect evidence is not a downloadable file. */
  }
  if (!safe) {
    await response.body?.cancel();
    throw new MarketplaceDownloadError(
      "MyMiniFactory did not return a supported file download. Download it on the source page and attach it in Pending Imports.",
    );
  }
  return readMarketplaceFile(response, candidate, "MyMiniFactory", signal, options.progress);
}
