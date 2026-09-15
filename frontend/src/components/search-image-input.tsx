import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { Camera, ImagePlus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function SearchImageInput({
  image,
  enabled,
  onChange,
}: {
  image: File | null;
  enabled: boolean;
  onChange: (image: File | null) => void;
}) {
  const { t } = useI18n();
  const input = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<{ file: File; url: string } | null>(null);
  const [error, setError] = useState<"invalid" | "multiple" | null>(null);
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    return () => {
      if (preview) URL.revokeObjectURL(preview.url);
    };
  }, [preview]);
  function select(files: FileList | File[]) {
    if (!enabled || !files.length) return;
    if (files.length !== 1) {
      setError("multiple");
      return;
    }
    const file = files[0];
    if (
      !file.size ||
      file.size > 8 * 1024 * 1024 ||
      !["image/png", "image/jpeg", "image/webp"].includes(file.type)
    ) {
      setError("invalid");
      return;
    }
    setError(null);
    setPreview({ file, url: URL.createObjectURL(file) });
    onChange(file);
  }
  function choose(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) select(event.target.files);
    event.target.value = "";
  }
  return (
    <section
      aria-label={t("aiSearch.imageDropZone")}
      className={`mb-5 rounded-lg border bg-muted/30 p-4 ${dragging && enabled ? "border-primary" : "border-border"}`}
      onDragEnter={(event) => {
        if (event.dataTransfer.types.includes("Files")) {
          event.preventDefault();
          if (enabled) setDragging(true);
        }
      }}
      onDragOver={(event) => {
        if (event.dataTransfer.types.includes("Files")) {
          event.preventDefault();
          event.dataTransfer.dropEffect = enabled ? "copy" : "none";
        }
      }}
      onDragLeave={(event) => {
        if (
          !(event.relatedTarget instanceof Node) ||
          !event.currentTarget.contains(event.relatedTarget)
        )
          setDragging(false);
      }}
      onDrop={(event) => {
        if (!event.dataTransfer.types.includes("Files")) return;
        event.preventDefault();
        setDragging(false);
        select(event.dataTransfer.files);
      }}
    >
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          {preview?.file === image && (
            <img
              src={preview.url}
              alt={t("aiSearch.queryImage")}
              className="h-20 w-20 shrink-0 rounded-md border border-border bg-background object-contain"
            />
          )}
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium">{t("aiSearch.imagePrompt")}</p>
            <p className="mt-1 max-w-prose text-xs leading-relaxed text-muted-foreground">
              {t("aiSearch.imagePrivacy")}
            </p>
            <p className="mt-1 text-xs text-muted-foreground">{t("aiSearch.imageLimits")}</p>
            {enabled && <p className="mt-2 text-sm text-foreground">{t("aiSearch.dropImage")}</p>}
          </div>
        </div>
        <input
          ref={input}
          type="file"
          hidden
          accept="image/png,image/jpeg,image/webp"
          aria-label={t("aiSearch.chooseImage")}
          disabled={!enabled}
          onChange={choose}
        />
        <input
          ref={camera}
          type="file"
          hidden
          accept="image/png,image/jpeg,image/webp"
          capture="environment"
          aria-label={t("aiSearch.takePhoto")}
          disabled={!enabled}
          onChange={choose}
        />
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Button
            className="min-h-11"
            variant="outline"
            disabled={!enabled}
            onClick={() => input.current?.click()}
          >
            <ImagePlus className="mr-2 h-4 w-4" />
            {t("aiSearch.chooseImage")}
          </Button>
          <Button
            className="min-h-11"
            variant="outline"
            disabled={!enabled}
            onClick={() => camera.current?.click()}
          >
            <Camera className="mr-2 h-4 w-4" aria-hidden />
            {t("aiSearch.takePhoto")}
          </Button>
          {image && (
            <Button
              variant="ghost"
              className="min-h-11 min-w-11"
              size="icon"
              aria-label={t("aiSearch.clearImage")}
              onClick={() => {
                onChange(null);
                setPreview(null);
                setError(null);
              }}
            >
              <X className="h-4 w-4" />
            </Button>
          )}
        </div>
      </div>
      {!enabled && (
        <p role="status" className="mt-3 text-sm text-muted-foreground">
          {t("aiSearch.visualUnavailable")}
        </p>
      )}
      {error && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {t(error === "multiple" ? "aiSearch.oneImage" : "aiSearch.invalidImage")}
        </p>
      )}
    </section>
  );
}
