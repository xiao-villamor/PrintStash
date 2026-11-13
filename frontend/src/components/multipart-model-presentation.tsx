import "./library-items.css";
import { Boxes } from "lucide-react";
import { useUiLocale } from "@/lib/i18n";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";
import { LibraryPreviewPlaceholder } from "@/components/library-card-placeholder";

export function Count({ count, one, many }: { count: number; one: string; many: string }) {
  useUiLocale();
  return <span>{(count === 1 ? one : many).replace("{count}", String(count))}</span>;
}

export function Cover({ src, alt }: { src: string | null; alt: string }) {
  useUiLocale();
  return (
    <ProtectedThumbnail
      path={src}
      alt={alt}
      fade
      loadingPlaceholder={<LibraryPreviewPlaceholder />}
      className="library-preview h-full w-full"
      imageClassName="h-full w-full object-contain"
      placeholder={
        <div className="flex h-full w-full items-center justify-center text-muted-foreground">
          <Boxes className="h-10 w-10" aria-hidden />
        </div>
      }
    />
  );
}
