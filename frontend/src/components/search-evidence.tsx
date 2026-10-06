import { useViewportAssetUrl } from "@/lib/use-viewport-admission";
import { Box, Boxes, FileText, Folder } from "lucide-react";
import { cn } from "@/lib/utils";
import type { SearchSubjectType } from "@/types/search";

export function SearchSubjectPreview({
  path,
  subjectType,
  large = false,
}: {
  path: string | null | undefined;
  subjectType: SearchSubjectType;
  large?: boolean;
}) {
  const { url: source, ref } = useViewportAssetUrl(path);
  const frame = large ? "aspect-[4/3] w-full" : "h-16 w-16 shrink-0";
  const Icon =
    subjectType === "collection"
      ? Folder
      : subjectType === "multipart_model"
        ? Boxes
        : subjectType === "document"
          ? FileText
          : Box;
  return source ? (
    <img
      ref={ref}
      src={source}
      alt=""
      loading="lazy"
      className={cn(frame, "rounded-md bg-muted/30 object-contain")}
    />
  ) : (
    <div
      ref={ref}
      className={cn(
        frame,
        "flex items-center justify-center rounded-md bg-muted/30 text-muted-foreground",
      )}
      aria-hidden
    >
      <Icon className={large ? "h-10 w-10" : "h-6 w-6"} />
    </div>
  );
}
