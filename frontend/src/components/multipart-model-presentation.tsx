import { Boxes } from "lucide-react";
import { useUiLocale } from "@/lib/i18n";
import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";

export function Count({ count, one, many }: { count: number; one: string; many: string }) {
  useUiLocale();
  return <span>{(count === 1 ? one : many).replace("{count}", String(count))}</span>;
}

export function Cover({ src, alt }: { src: string | null; alt: string }) {
  useUiLocale();
  const external = src?.startsWith("https://") || src?.startsWith("http://") ? src : null;
  const authenticated = useAuthenticatedAssetUrl(external ? null : src);
  const url = external ?? authenticated;
  return url ? (
    <img
      data-library-thumbnail="ready"
      src={url}
      alt={alt}
      className="h-full w-full object-contain"
    />
  ) : (
    <div
      data-library-thumbnail={src ? "pending" : "missing"}
      className="flex h-full w-full items-center justify-center bg-muted text-muted-foreground"
    >
      <Boxes className="h-10 w-10" aria-hidden />
    </div>
  );
}
