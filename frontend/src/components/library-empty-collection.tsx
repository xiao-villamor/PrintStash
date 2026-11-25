import { Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";

/** A stack of model cards waiting for a first Model: quiet, drawn from the theme's own colors. */
function EmptyCollectionArt() {
  return (
    <svg
      viewBox="0 0 200 140"
      width="200"
      height="140"
      aria-hidden="true"
      className="text-muted-foreground"
    >
      <ellipse cx="100" cy="126" rx="64" ry="7" fill="currentColor" opacity="0.12" />
      <g transform="rotate(-9 100 72)">
        <rect
          x="44"
          y="30"
          width="68"
          height="86"
          rx="8"
          className="fill-card"
          stroke="currentColor"
          strokeWidth="2"
          opacity="0.35"
        />
      </g>
      <g transform="rotate(8 100 72)">
        <rect
          x="88"
          y="26"
          width="68"
          height="86"
          rx="8"
          className="fill-card"
          stroke="currentColor"
          strokeWidth="2"
          opacity="0.55"
        />
      </g>
      <rect
        x="66"
        y="30"
        width="68"
        height="86"
        rx="8"
        className="fill-card"
        stroke="currentColor"
        strokeWidth="2"
      />
      <g
        className="text-primary"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M100 50l17 10v20l-17 10-17-10V60z" />
        <path d="M83 60l17 10 17-10M100 70v20" />
      </g>
      <rect
        x="76"
        y="100"
        width="32"
        height="4"
        rx="2"
        fill="currentColor"
        opacity="0.25"
      />
      <circle cx="136" cy="112" r="13" className="fill-primary" />
      <path
        d="M136 106v12M130 112h12"
        stroke="white"
        strokeWidth="2.5"
        strokeLinecap="round"
        fill="none"
      />
    </svg>
  );
}

/** What an empty collection offers: put the first Model here, by button or by dropping files. */
export function LibraryEmptyCollection({
  disabled,
  onUpload,
}: {
  disabled: boolean;
  onUpload: () => void;
}) {
  useUiLocale();
  return (
    <div className="flex flex-1 items-center justify-center px-4 py-12 sm:px-6">
      <section
        aria-labelledby="empty-collection-title"
        className="flex w-full max-w-xl flex-col items-center gap-5 rounded-lg border border-dashed border-border-strong bg-card/40 px-8 py-10 text-center"
      >
        <EmptyCollectionArt />
        <div className="space-y-1.5">
          <h2 id="empty-collection-title" className="text-lg font-semibold text-foreground">
            {uiText("library.collectionEmpty")}
          </h2>
          <p className="mx-auto max-w-sm text-sm text-muted-foreground">
            {uiText("library.collectionEmptyHelp")}
          </p>
        </div>
        <Button type="button" disabled={disabled} onClick={onUpload}>
          <Upload className="size-4" aria-hidden="true" />
          {uiText("Upload files")}
        </Button>
        <p className="text-xs text-muted-foreground">
          {uiText("library.collectionEmptyDrop")}
        </p>
      </section>
    </div>
  );
}
