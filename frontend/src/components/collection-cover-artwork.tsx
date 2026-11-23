import { Box, Folder } from "lucide-react";
import { LibraryPreviewPlaceholder } from "@/components/library-card-placeholder";
import { ProtectedThumbnail } from "@/components/protected-thumbnail";

export function CollectionCoverArtwork({
  models,
  image,
  compact = false,
  pending = false,
  state,
  className,
}: {
  models?: { key: string; thumbnail: string | null | undefined }[];
  image?: string | null;
  compact?: boolean;
  pending?: boolean;
  state?: "ready" | "pending" | "failed";
  className: string;
}) {
  const count = image ? 1 : (models?.length ?? (pending ? 4 : 0));
  const folder = <Folder className={`${compact ? "size-5" : "size-8"} text-muted-foreground`} />;
  return (
    <div
      data-library-preview={state}
      className={`library-preview grid gap-px overflow-hidden ${count > 1 ? "grid-cols-2" : "grid-cols-1"} ${count > 2 ? "grid-rows-2" : "grid-rows-1"} ${className}`}
      aria-hidden="true"
    >
      {image ? (
        <ProtectedThumbnail
          path={image}
          alt=""
          fade
          priority="background"
          loadingPlaceholder={<LibraryPreviewPlaceholder />}
          placeholder={folder}
          className="flex min-h-0 min-w-0 items-center justify-center overflow-hidden"
          imageClassName="h-full w-full object-cover"
        />
      ) : models && models.length > 0 ? (
        models.map((model, index) => (
          <ProtectedThumbnail
            key={model.key}
            path={model.thumbnail}
            alt=""
            fade
            priority="background"
            loadingPlaceholder={<LibraryPreviewPlaceholder />}
            className={`flex min-h-0 min-w-0 items-center justify-center overflow-hidden ${count === 3 && index === 0 ? "row-span-2" : ""}`}
            imageClassName="h-full w-full object-contain"
            placeholder={
              <Box className={`${compact ? "size-3" : "size-6"} text-muted-foreground`} />
            }
          />
        ))
      ) : pending ? (
        Array.from({ length: 4 }, (_, index) => (
          <div key={index} className="library-loading-placeholder min-h-0">
            <LibraryPreviewPlaceholder />
          </div>
        ))
      ) : (
        <div className="col-span-full grid place-items-center bg-muted/30">{folder}</div>
      )}
    </div>
  );
}
