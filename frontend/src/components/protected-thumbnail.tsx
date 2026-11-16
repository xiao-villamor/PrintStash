import { useCallback, useState, type ReactNode } from "react";
import { peekCachedAssetUrl } from "@/lib/asset-cache";
import { useAuthenticatedAsset } from "@/lib/use-authenticated-asset-url";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { cn } from "@/lib/utils";

/** Viewport admission owns laziness; an admitted image decodes without another visibility gate. */
export function ProtectedThumbnail({
  path,
  alt,
  placeholder,
  className,
  imageClassName,
  fade = false,
  previewView,
  children,
}: {
  path: string | null | undefined;
  alt: string;
  placeholder: ReactNode;
  className?: string;
  imageClassName?: string;
  fade?: boolean;
  /** Names the presented Model view, when this image is one of several. */
  previewView?: string;
  children?: ReactNode;
}) {
  const { ref, admitted } = useViewportAdmission();
  const external = path?.startsWith("https://") || path?.startsWith("http://") ? path : null;
  const asset = useAuthenticatedAsset(external ? null : path, admitted);
  // Capture reuse before admission starts a download. A remounted cached image
  // stays visible; a replacement blob still gets its own reveal.
  const [cachedSource, setCachedSource] = useState(() => ({
    path,
    url: path && !external ? peekCachedAssetUrl(path) : null,
  }));
  if (cachedSource.path !== path) {
    setCachedSource({ path, url: path && !external ? peekCachedAssetUrl(path) : null });
  }
  const url = admitted ? (external ?? asset.url) : null;
  const [decoded, setDecoded] = useState<string | null>(null);
  const ready = url !== null && decoded === url;
  const reveal = fade && url !== cachedSource.url;
  const [failedSource, setFailedSource] = useState<string | null>(null);
  const failed = asset.status === "failed" || (url !== null && failedSource === url);

  const decode = useCallback(async (image: HTMLImageElement, loaded = false) => {
    const source = image.getAttribute("src");
    if (!source) return;
    const finalAttempt = loaded || image.complete;
    try {
      // decode() exists on the supported browser floor. jsdom has no decoder.
      await image.decode?.();
      if (image.isConnected && image.getAttribute("src") === source) {
        setDecoded(source);
        setFailedSource((failed) => (failed === source ? null : failed));
      }
    } catch {
      // A newly assigned image can reject decode before its load starts.
      // Its load/error event owns the final outcome; a valid retry clears failure.
      if (finalAttempt && image.isConnected && image.getAttribute("src") === source)
        setFailedSource(source);
    }
  }, []);
  const bindImage = useCallback(
    (image: HTMLImageElement | null) => {
      if (!image || image.getAttribute("src") !== url) return;
      // Admission already owns network priority. Start decoding as soon as this
      // source is attached, without waiting for a later image load event.
      if (image.decode !== undefined || (image.complete && image.naturalWidth > 0))
        void decode(image);
    },
    [decode, url],
  );

  return (
    <div
      ref={ref}
      className={cn(fade && "relative", className)}
      data-library-thumbnail={!path ? "missing" : failed ? "failed" : ready ? "ready" : "pending"}
    >
      {url ? (
        <img
          key={url}
          src={url}
          alt={alt}
          data-preview-view={previewView}
          draggable={false}
          loading="eager"
          decoding="async"
          className={cn(
            imageClassName,
            reveal && "thumbnail-reveal transition-opacity duration-slow ease-out",
            fade && (failed || (reveal && !ready) ? "opacity-0" : "opacity-100"),
          )}
          onError={(event) => setFailedSource(event.currentTarget.getAttribute("src"))}
          onLoad={(event) => {
            void decode(event.currentTarget, true);
          }}
          ref={bindImage}
        />
      ) : (
        placeholder
      )}
      {url && fade && (
        <div
          aria-hidden
          className={cn(
            "pointer-events-none absolute inset-0 transition-opacity duration-slow ease-out",
            failed || (reveal && !ready) ? "opacity-100" : "opacity-0",
          )}
        >
          {placeholder}
        </div>
      )}
      {children}
    </div>
  );
}
