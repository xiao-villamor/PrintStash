import { useId, type CSSProperties, type ReactNode } from "react";
import { Check } from "lucide-react";
import { ModelCard } from "@/components/model-card";
import { SettingsSwitch } from "@/components/settings-card";
import { useI18n } from "@/lib/i18n";
import { previewLookLabel, previewViewLabel } from "@/lib/preview-appearance";
import { PREVIEW_LOOK_COLOURS } from "@/lib/thumbnail-profile.generated";
import { cn } from "@/lib/utils";
import type { PreviewAppearance as Appearance, PreviewLook, PreviewMode } from "@/types/config";
import type { ModelListItem } from "@/types/models";
import { NO_PREVIEWS } from "@/types/previews";

const lookOrder: readonly PreviewLook[] = [
  "sky",
  "printstash_blue",
  "slate",
  "graphite",
  "sand",
  "sage",
  "clay",
  "plum",
];
const previewModes = ["single", "oblique", "oblique-poles"] as const;

function swatchStyle(look: PreviewLook): CSSProperties & { "--preview-swatch": string } {
  return { "--preview-swatch": PREVIEW_LOOK_COLOURS[look] };
}

export function PreviewAppearance({
  value,
  onChange,
  layout,
  disabled,
  status,
  firstRun = false,
  views,
}: {
  value: Appearance;
  onChange: (value: Appearance) => void;
  layout: "settings" | "setup";
  disabled?: boolean;
  status?: ReactNode;
  firstRun?: boolean;
  views?: { value: PreviewMode; onChange: (value: PreviewMode) => void };
}) {
  const { t } = useI18n();
  const themes = [
    { value: "light", label: t("Light") },
    { value: "dark", label: t("Dark") },
  ] as const;
  const colourLabel = useId();
  const colourHint = useId();
  const gridHint = useId();
  const viewsId = useId();
  const row =
    layout === "settings"
      ? "settings-preference-row"
      : "flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-border py-3 text-sm font-medium";
  const PreviewHeading = layout === "settings" ? "h4" : "h3";
  const sampleName = t("Example bracket");
  const exampleEndpoint = firstRun
    ? "/api/v1/setup/preview-example"
    : "/api/v1/config/preview-example";
  const sample: ModelListItem = {
    id: -1,
    edit_epoch: "sample",
    edit_version: 1,
    name: sampleName,
    slug: "example-bracket",
    collection: null,
    collection_id: null,
    collection_label: null,
    source_url: null,
    effective_role: null,
    tags: [],
    thumbnail_url: `${exampleEndpoint}?look=${value.preview_look}&floor_grid=${value.preview_floor_grid}`,
    file_count: 1,
    gcode_file_count: 0,
    mesh_file_id: null,
    bounding_box: { x_mm: 48, y_mm: 32, z_mm: 24 },
    printer_presence: [],
    updated_at: "2026-01-01T12:00:00Z",
    print_summary: null,
    previews: NO_PREVIEWS,
    starred: false,
  };

  return (
    <div className={layout === "settings" ? "settings-preference-fields" : undefined}>
      <div className={row}>
        <div>
          <p id={colourLabel}>{t("Colour")}</p>
          {layout === "settings" && (
            <span id={colourHint} className="settings-row-hint">
              {t("Applied to the model in generated images.")}
            </span>
          )}
        </div>
        <div className="preview-palette flex min-w-0 flex-wrap items-center gap-3 md:justify-end">
          <span aria-hidden className="whitespace-nowrap text-xs text-muted-foreground">
            {t(previewLookLabel[value.preview_look])}
          </span>
          <div
            role="radiogroup"
            aria-labelledby={colourLabel}
            aria-describedby={layout === "settings" ? colourHint : undefined}
            className="grid grid-cols-4 gap-2 sm:grid-cols-8"
          >
            {lookOrder.map((look) => (
              <label
                key={look}
                title={t(previewLookLabel[look])}
                className="relative block has-disabled:cursor-disabled"
              >
                <input
                  type="radio"
                  name={colourLabel}
                  aria-label={t(previewLookLabel[look])}
                  className="peer sr-only"
                  checked={value.preview_look === look}
                  onChange={() => onChange({ ...value, preview_look: look })}
                  disabled={disabled}
                />
                <span
                  style={swatchStyle(look)}
                  className="flex size-8 items-center justify-center rounded-full border border-border-strong bg-preview-swatch transition-shadow duration-press peer-checked:ring-2 peer-checked:ring-ring peer-checked:ring-offset-1 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ring peer-disabled:opacity-60"
                  aria-hidden
                >
                  {value.preview_look === look && (
                    <Check
                      strokeWidth={3}
                      className="size-5 rounded-full bg-inverse-surface p-0.5 text-inverse-on-surface"
                    />
                  )}
                </span>
              </label>
            ))}
          </div>
        </div>
      </div>
      <div className={row}>
        <div>
          <p>{t("Floor grid")}</p>
          {layout === "settings" && (
            <span id={gridHint} className="settings-row-hint">
              {t("Millimetre grid under the model.")}
            </span>
          )}
        </div>
        <SettingsSwitch
          label={t("Floor grid")}
          descriptionId={layout === "settings" ? gridHint : undefined}
          checked={value.preview_floor_grid}
          onChange={(preview_floor_grid) => onChange({ ...value, preview_floor_grid })}
          disabled={disabled}
        />
      </div>
      {views && (
        <div className={row}>
          <label htmlFor={viewsId}>{t("setup.previewViews")}</label>
          <select
            id={viewsId}
            className="h-9 w-full px-3 font-normal sm:w-56"
            value={views.value}
            disabled={disabled}
            onChange={(event) => {
              const mode = previewModes.find((option) => option === event.target.value);
              if (mode) views.onChange(mode);
            }}
          >
            {previewModes.map((mode) => (
              <option key={mode} value={mode}>
                {t(previewViewLabel[mode])}
              </option>
            ))}
          </select>
        </div>
      )}
      <div className={cn("space-y-3 pt-4", layout === "settings" && "pb-3")}>
        <PreviewHeading className="text-sm font-medium text-foreground">
          {t("Preview")}
        </PreviewHeading>
        <div
          className={cn(
            "grid grid-cols-2 gap-4",
            layout === "settings" ? "max-w-[30rem]" : "mx-auto max-w-[41rem]",
          )}
        >
          {themes.map((theme) => (
            <figure key={theme.value} className="m-0 min-w-0 space-y-1.5">
              <figcaption className="text-xs text-muted-foreground">{theme.label}</figcaption>
              <div
                data-preview-theme={theme.value}
                className="text-foreground"
                role="img"
                aria-label={sampleName}
              >
                <div inert>
                  <ModelCard model={sample} />
                </div>
              </div>
            </figure>
          ))}
        </div>
        {status}
      </div>
    </div>
  );
}
