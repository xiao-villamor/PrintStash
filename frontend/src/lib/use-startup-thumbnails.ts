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
    let alive = true;
    const decoded = new WeakSet<HTMLImageElement>();
    const decoding = new WeakSet<HTMLImageElement>();
    const check = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const thumbnails = [...element.querySelectorAll("[data-library-thumbnail]")];
        if (hasMedia && thumbnails.length === 0) return;
        const visible = thumbnails.filter((thumbnail) => {
          const bounds = thumbnail.getBoundingClientRect();
          return (
            bounds.width > 0 &&
            bounds.height > 0 &&
            bounds.top < innerHeight &&
            bounds.bottom > 0 &&
            bounds.left < innerWidth &&
            bounds.right > 0
          );
        });
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
          if (!decoded.has(image) && !decoding.has(image)) {
            decoding.add(image);
            Promise.resolve(image.decode?.()).then(
              () => {
                if (!alive) return;
                decoded.add(image);
                check();
              },
              () => {
                if (alive) settle("media", "failed");
              },
            );
          }
          return decoded.has(image);
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
      attributeFilter: ["data-library-thumbnail", "src"],
    });
    element.addEventListener("load", check, true);
    element.addEventListener("error", check, true);
    window.addEventListener("resize", check);
    check();
    return () => {
      alive = false;
      observer.disconnect();
      element.removeEventListener("load", check, true);
      element.removeEventListener("error", check, true);
      window.removeEventListener("resize", check);
      cancelAnimationFrame(frame);
    };
  }, [root, ready, hasMedia, settle]);
}
