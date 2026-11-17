import { readBoundedMetadataResponse } from "./capture-adapter.ts";
import { sourcePublication, type SourceProviderCode } from "./source-providers.ts";
import { readMarketplaceFile } from "./marketplace-download.ts";

const imageHosts: Record<SourceProviderCode, readonly string[]> = {
  makerworld: ["makerworld.bblmw.com"],
  printables: ["media.printables.com", "media.prusaprinters.org"],
  thingiverse: ["cdn.thingiverse.com", "resize.thingiverse.com"],
  myminifactory: ["images.myminifactory.com", "images2.myminifactory.com", "cdn.myminifactory.com"],
  cults: ["images.cults3d.com", "files.cults3d.com", "fbi.cults3d.com"],
  thangs: ["thangs.com", "www.thangs.com"],
  crealitycloud: ["pic2-cdn.creality.com", "pic1-cdn.creality.com"],
  makeronline: ["cdn-acop.makeronline.com"],
};
export interface SourceGalleryImage {
  id: string;
  filename: string;
  url: string;
}

/** Runs in the page. Returns only image URLs from the publication gallery, never page state. */
export function readSourceGalleryInMainWorld(provider: SourceProviderCode): string[] {
  const selectors: Record<SourceProviderCode, string> = {
    makerworld: ".swiper-fade .swiper-slide img",
    printables: '[class*="model-detail"] [class*="gallery"] img, [class*="model-gallery"] img',
    thingiverse: '[class*="CarouselThumbnails__carouselThumb"] img',
    myminifactory: '.slick-slide:not(.slick-cloned) img[alt$=" image"]',
    cults: '[data-controller~="gallery"] img, .creation-gallery img',
    thangs: '.swiper-wrapper [class*="ModelImageCarousel_Slide"] img',
    crealitycloud: ".swiper-slide .cover img",
    makeronline: ".swiper-banner img, .swiper .item .template-image-default img",
  };
  const values = Array.from(document.querySelectorAll<HTMLImageElement>(selectors[provider]))
    .slice(0, 128)
    .map((image) => image.currentSrc || image.src);
  // Only top-level publication objects: do not traverse recommendations, authors or reviews.
  for (const script of Array.from(
    document.querySelectorAll('script[type="application/ld+json"]'),
  ).slice(0, 8)) {
    const text = script.textContent ?? "";
    if (text.length > 256_000) continue;
    try {
      const parsed = JSON.parse(text);
      const records = Array.isArray(parsed) ? parsed : (parsed?.["@graph"] ?? [parsed]);
      if (!Array.isArray(records)) continue;
      for (const record of records.slice(0, 32)) {
        if (!record || !["Product", "3DModel", "CreativeWork"].includes(record["@type"])) continue;
        if (
          typeof record.url === "string" &&
          new URL(record.url, location.href).pathname.replace(/\/files\/?$/, "") !==
            location.pathname.replace(/\/files\/?$/, "")
        )
          continue;
        for (const value of [record.image].flat().slice(0, 128)) {
          const url = typeof value === "string" ? value : (value?.contentUrl ?? value?.url);
          if (typeof url === "string") values.push(url);
        }
      }
    } catch {
      /* Malformed publication metadata is not a gallery. */
    }
  }
  if (values.length === 0) {
    const hero = document.querySelector<HTMLMetaElement>('meta[property="og:image"]')?.content;
    if (hero) values.push(hero);
  }
  return [...new Set(values.filter((value) => value.length <= 4096))].slice(0, 129);
}

/** Validate again in extension context. URL identities are hashed before persistence. */
export async function sourceGallery(pageUrl: string, raw: unknown): Promise<SourceGalleryImage[]> {
  const publication = sourcePublication(pageUrl);
  if (!publication || !Array.isArray(raw) || raw.length > 129)
    throw new Error("Invalid publication gallery.");
  const unique = new Map<string, string>();
  for (const value of raw) {
    if (typeof value !== "string" || value.length > 4096) continue;
    let url: URL;
    try {
      url = new URL(value, pageUrl);
    } catch {
      continue;
    }
    if (
      url.protocol !== "https:" ||
      url.port ||
      url.username ||
      url.password ||
      url.hash ||
      !imageHosts[publication.code].includes(url.hostname)
    )
      continue;
    // Thingiverse's resize URL wraps the original CDN image; retain that image once.
    if (url.hostname === "resize.thingiverse.com") {
      const original = url.searchParams.get("src");
      if (original) {
        try {
          const candidate = new URL(original);
          if (
            candidate.protocol === "https:" &&
            candidate.hostname === "cdn.thingiverse.com" &&
            !candidate.port &&
            !candidate.username &&
            !candidate.password &&
            !candidate.hash
          )
            url = candidate;
        } catch {
          continue;
        }
      }
    }
    let identity = `${url.origin}${url.pathname}`;
    if (publication.code === "thangs" && url.pathname === "/_next/image") {
      const wrapped = url.searchParams.get("url");
      if (!wrapped) continue;
      try {
        const original = new URL(wrapped, url.origin);
        if (
          original.protocol !== "https:" ||
          original.username ||
          original.password ||
          original.port
        )
          continue;
        // Keep requests on the approved provider image proxy; hash only the source identity.
        identity += `?url=${original.origin}${original.pathname}`;
      } catch {
        continue;
      }
    }
    if (!unique.has(identity)) unique.set(identity, url.href);
  }
  if (unique.size > 64)
    throw new Error(
      "This publication has more than 64 images. Choose a smaller gallery before capturing.",
    );
  return Promise.all(
    [...unique].map(async ([identity, url], index) => {
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(identity));
      return {
        id: [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join(""),
        filename: `source-image-${index + 1}`,
        url,
      };
    }),
  );
}

export async function downloadSourceImage(
  image: SourceGalleryImage,
  signal?: AbortSignal,
  fetchImpl: typeof fetch = fetch,
) {
  const response = await fetchImpl(image.url, { credentials: "omit", redirect: "error", signal });
  const mediaType = response.headers.get("Content-Type")?.split(";")[0]?.trim().toLowerCase();
  if (!mediaType || !["image/jpeg", "image/png", "image/webp", "image/gif"].includes(mediaType)) {
    await response.body?.cancel();
    throw new Error(
      "The provider did not return a supported gallery image. Refresh the gallery or deselect this image.",
    );
  }
  const file = await readMarketplaceFile(
    response,
    { id: image.id, filename: image.filename, fileType: "other" },
    "Gallery",
    signal,
    undefined,
    15 * 1024 * 1024,
  );
  return { ...file, mediaType };
}

/** Printables publishes the complete image list separately from its lazy page thumbnails. */
export async function readPrintablesGallery(
  pageUrl: string,
  signal?: AbortSignal,
  fetchImpl: typeof fetch = fetch,
): Promise<SourceGalleryImage[]> {
  const publication = sourcePublication(pageUrl);
  if (publication?.code !== "printables") throw new Error("Invalid Printables publication.");
  const response = await fetchImpl("https://api.printables.com/graphql/", {
    method: "POST",
    credentials: "omit",
    redirect: "error",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: "query ($id: ID!) { print(id: $id) { id images { filePath } } }",
      variables: { id: publication.itemId },
    }),
  });
  if (!response.ok) throw new Error("Printables gallery is unavailable.");
  const raw: unknown = JSON.parse(await readBoundedMetadataResponse(response, 256 * 1024, signal));
  if (
    !raw ||
    typeof raw !== "object" ||
    "errors" in raw ||
    !("data" in raw) ||
    !raw.data ||
    typeof raw.data !== "object" ||
    !("print" in raw.data) ||
    !raw.data.print ||
    typeof raw.data.print !== "object"
  )
    throw new Error("Printables gallery response changed.");
  const item = raw.data.print;
  if (
    !("id" in item) ||
    String(item.id) !== publication.itemId ||
    !("images" in item) ||
    !Array.isArray(item.images) ||
    item.images.length > 64
  )
    throw new Error("Printables gallery response changed or exceeds 64 images.");
  const urls = item.images.map((image: unknown) => {
    if (
      !image ||
      typeof image !== "object" ||
      !("filePath" in image) ||
      typeof image.filePath !== "string" ||
      image.filePath.length > 4096 ||
      !image.filePath.startsWith(`media/prints/${publication.itemId}/images/`) ||
      /[?#\\]|(?:^|\/)\.\.(?:\/|$)|%(?:2e|2f|5c)/i.test(image.filePath)
    )
      throw new Error("Printables returned an invalid image path.");
    return `https://media.printables.com/${image.filePath}`;
  });
  return sourceGallery(pageUrl, urls);
}
