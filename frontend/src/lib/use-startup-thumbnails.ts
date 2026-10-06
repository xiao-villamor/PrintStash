import { useEffect, type RefObject } from "react";
import { markStartup } from "@/lib/startup-timing";

/** Keep image completion separate from navigable content, including absent derivatives. */
export function useStartupThumbnails(root: RefObject<HTMLElement | null>, ready: boolean) {
  useEffect(() => {
    const element = root.current;
    if (!element || !ready) return;
    let frame = 0;
    const check = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const visible = [...element.querySelectorAll("[data-library-thumbnail]")].filter(
          (thumbnail) => {
            const bounds = thumbnail.getBoundingClientRect();
            return (
              bounds.width > 0 && bounds.height > 0 && bounds.top < innerHeight && bounds.bottom > 0
            );
          },
        );
        if (
          visible.every((thumbnail) => {
            const image =
              thumbnail instanceof HTMLImageElement ? thumbnail : thumbnail.querySelector("img");
            return (
              thumbnail.getAttribute("data-library-thumbnail") === "ready" &&
              image?.complete &&
              image.naturalWidth > 0
            );
          })
        )
          markStartup("library-thumbnails");
      });
    };
    const observer = new MutationObserver(check);
    observer.observe(element, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ["data-library-thumbnail"],
    });
    element.addEventListener("load", check, true);
    check();
    return () => {
      observer.disconnect();
      element.removeEventListener("load", check, true);
      cancelAnimationFrame(frame);
    };
  }, [root, ready]);
}
