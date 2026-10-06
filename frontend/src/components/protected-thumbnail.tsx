import { useState, type ReactNode } from "react";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { cn } from "@/lib/utils";

/** A persistent viewport target, protected URL lease and decoded-image readiness. */
export function ProtectedThumbnail({
  path,
  alt,
  placeholder,
  className,
  imageClassName,
  fade = false,
  children,
}: {
  path: string | null | undefined;
  alt: string;
  placeholder: ReactNode;
  className?: string;
  imageClassName?: string;
  fade?: boolean;
  children?: ReactNode;
}) {
  const { ref, admitted } = useViewportAdmission();
  const external = path?.startsWith("https://") || path?.startsWith("http://") ? path : null;
  const protectedUrl = useAuthenticatedAssetUrl(external ? null : path, admitted);
  const url = admitted ? (external ?? protectedUrl) : null;
  const [decoded, setDecoded] = useState<string | null>(null);
  const ready = url !== null && decoded === url;

  async function decode(image: HTMLImageElement) {
    const source = image.getAttribute("src");
    if (!source) return;
    try {
      // decode() exists on the supported browser floor. jsdom has no decoder.
      await image.decode?.();
      if (image.isConnected && image.getAttribute("src") === source) setDecoded(source);
    } catch {
      // A failed decode must not claim startup's decoded-image milestone.
    }
  }

  return (
    <div
      ref={ref}
      className={className}
      data-library-thumbnail={!path ? "missing" : ready ? "ready" : "pending"}
    >
      {url ? (
        <img
          src={url}
          alt={alt}
          draggable={false}
          loading="lazy"
          decoding="async"
          className={cn(
            imageClassName,
            fade && "transition-opacity duration-slow ease-out",
            fade && (ready ? "opacity-90 group-hover:opacity-100" : "opacity-0"),
          )}
          onLoad={(event) => {
            void decode(event.currentTarget);
          }}
          ref={(image) => {
            if (image?.complete && image.naturalWidth > 0) void decode(image);
          }}
        />
      ) : (
        placeholder
      )}
      {children}
    </div>
  );
}
