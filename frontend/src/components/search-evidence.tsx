import { useAuthenticatedAssetUrl } from "@/lib/use-authenticated-asset-url";
import { Box } from "lucide-react";
import { cn } from "@/lib/utils";

export function SearchModelPreview({
  path,
  large = false,
}: {
  path: string | null | undefined;
  large?: boolean;
}) {
  const source = useAuthenticatedAssetUrl(path);
  const frame = large ? "aspect-[4/3] w-full" : "h-16 w-16 shrink-0";
  return source ? (
    <img
      src={source}
      alt=""
      loading="lazy"
      className={cn(frame, "rounded-md bg-muted/30 object-contain")}
    />
  ) : large ? (
    <div
      className={cn(
        frame,
        "flex items-center justify-center rounded-md bg-muted/30 text-muted-foreground",
      )}
      aria-hidden
    >
      <Box className="h-10 w-10" />
    </div>
  ) : null;
}
