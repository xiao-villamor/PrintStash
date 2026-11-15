import { useEffect, type RefObject } from "react";
import { markStartup } from "@/lib/startup-timing";
import { useLibraryStartup } from "@/lib/library-startup-context";

/** Observe the current content only; an absent derivative is a deliberate placeholder. */
export function useStartupThumbnails(
  root: RefObject<HTMLElement | null>,
  ready: boolean,
  hasMedia = false,
) {
  const { settle } = useLibraryStartup();
  useEffect(() => {
    const element = root.current;
    if (!element || !ready) return;
    let frame = 0;
    const check = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const thumbnails = [...element.querySelectorAll("[data-library-thumbnail]")];
        const inViewport = (thumbnail: Element) => {
          const bounds = thumbnail.getBoundingClientRect();
          return (
            bounds.width > 0 &&
            bounds.height > 0 &&
            bounds.top < innerHeight &&
            bounds.bottom > 0 &&
            bounds.left < innerWidth &&
            bounds.right > 0
          );
        };
        // Mosaic metadata and revision details share the browser's connection
        // pool with thumbnails. Release them after the first visible Model row
        // settles, independently of lower rows and the mosaics themselves.
        const cards = [
          ...element.querySelectorAll(
            '[data-library-key^="/models/"], [data-library-key^="/multipart-models/"]',
          ),
        ].filter(inViewport);
        const top = Math.min(...cards.map((card) => card.getBoundingClientRect().top));
        const row = cards.filter((card) => Math.abs(card.getBoundingClientRect().top - top) < 1);
        if (
          row.every((card) => {
            const thumbnail = card.querySelector("[data-library-thumbnail]");
            const status = thumbnail?.getAttribute("data-library-thumbnail");
            // A missing/failed derivative must not strand the remaining reads.
            if (status === "missing" || status === "failed") return true;
            const image = thumbnail?.querySelector("img");
            return status === "ready" && image?.complete && image.naturalWidth > 0;
          })
        )
          settle("primary-media", "ready");
        if (hasMedia && thumbnails.length === 0) return;
        // Collection images do not exist until their bounded preview read resolves.
        // Keep secondary requests behind that read as well as the image bytes.
        const previews = [...element.querySelectorAll("[data-library-preview]")].filter(inViewport);
        if (previews.some((preview) => preview.getAttribute("data-library-preview") === "failed")) {
          settle("media", "failed");
          return;
        }
        if (previews.some((preview) => preview.getAttribute("data-library-preview") === "pending"))
          return;
        const visible = thumbnails.filter(inViewport);
        if (
          visible.some((thumbnail) => thumbnail.getAttribute("data-library-thumbnail") === "failed")
        ) {
          settle("media", "failed");
          return;
        }
        const complete = visible.every((thumbnail) => {
          const status = thumbnail.getAttribute("data-library-thumbnail");
          if (status === "missing") return true;
          const image =
            thumbnail instanceof HTMLImageElement ? thumbnail : thumbnail.querySelector("img");
          if (status !== "ready" || !image?.complete || image.naturalWidth <= 0) return false;
          // ProtectedThumbnail publishes ready only after decoding this source.
          // Decoding again here made every() serialize images across animation
          // frames, although their bytes were already ready for painting.
          return true;
        });
        if (complete) {
          settle("media", "ready");
          if (
            visible.every(
              (thumbnail) => thumbnail.getAttribute("data-library-thumbnail") === "ready",
            )
          )
            markStartup("library-thumbnails");
        }
      });
    };
    const observer = new MutationObserver(check);
    observer.observe(element, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-library-thumbnail", "data-library-preview", "src"],
    });
    element.addEventListener("load", check, true);
    element.addEventListener("error", check, true);
    window.addEventListener("resize", check);
    check();
    return () => {
      observer.disconnect();
      element.removeEventListener("load", check, true);
      element.removeEventListener("error", check, true);
      window.removeEventListener("resize", check);
      cancelAnimationFrame(frame);
    };
  }, [root, ready, hasMedia, settle]);
}
