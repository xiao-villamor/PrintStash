import { useId, type CSSProperties, type ReactNode } from "react";
import { Check } from "lucide-react";
import { ModelCard } from "@/components/model-card";
import { SettingsSwitch } from "@/components/settings-card";
import { useI18n } from "@/lib/i18n";
import { PREVIEW_LOOK_COLOURS } from "@/lib/thumbnail-profile.generated";
import { cn } from "@/lib/utils";
import type { PreviewAppearance as Appearance, PreviewLook } from "@/types/config";
import type { ModelListItem } from "@/types/models";
import { NO_PREVIEWS } from "@/types/previews";

const looks = {
  sky: "Sky",
  printstash_blue: "Blue",
  slate: "Slate",
  graphite: "Graphite",
  sand: "Sand",
} as const satisfies Record<PreviewLook, string>;

const lookOrder: readonly PreviewLook[] = ["sky", "printstash_blue", "slate", "graphite", "sand"];

const themes = [
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
] as const;

function swatchStyle(look: PreviewLook): CSSProperties & { "--preview-swatch": string } {
  return { "--preview-swatch": PREVIEW_LOOK_COLOURS[look] };
}

export function PreviewAppearance({
  value,
  onChange,
  layout,
  disabled,
  status,
}: {
  value: Appearance;
  onChange: (value: Appearance) => void;
  layout: "settings" | "setup";
  disabled?: boolean;
  status?: ReactNode;
}) {
  const { t } = useI18n();
  const colourLabel = useId();
  const colourHint = useId();
  const gridHint = useId();
  const row =
    layout === "settings"
      ? "settings-preference-row"
      : "flex flex-wrap items-center justify-between gap-x-6 gap-y-2 border-b border-border py-3 text-sm font-medium";
  const PreviewHeading = layout === "settings" ? "h4" : "h3";
  const sampleName = t("Example bracket");
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
    thumbnail_url: `/api/v1/config/preview-example?look=${value.preview_look}&floor_grid=${value.preview_floor_grid}`,
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
        <div className="flex min-w-0 items-center gap-3 md:justify-end">
          <span aria-hidden className="whitespace-nowrap text-xs text-muted-foreground">
            {t(looks[value.preview_look])}
          </span>
          <div
            role="radiogroup"
            aria-labelledby={colourLabel}
            aria-describedby={layout === "settings" ? colourHint : undefined}
            className="flex shrink-0 gap-2"
          >
            {lookOrder.map((look) => (
              <label
                key={look}
                title={t(looks[look])}
                className="relative block cursor-pointer has-disabled:cursor-disabled"
              >
                <input
                  type="radio"
                  name={colourLabel}
                  aria-label={t(looks[look])}
                  className="peer sr-only"
                  checked={value.preview_look === look}
                  onChange={() => onChange({ ...value, preview_look: look })}
                  disabled={disabled}
                />
                <span
                  style={swatchStyle(look)}
                  className="flex size-7 items-center justify-center border border-border-strong bg-preview-swatch transition-shadow duration-press peer-checked:ring-2 peer-checked:ring-ring peer-checked:ring-offset-1 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-ring peer-disabled:opacity-60"
                  aria-hidden
                >
                  {value.preview_look === look && (
                    <span className="grid size-4 place-items-center bg-card text-card-foreground">
                      <Check className="size-3" />
                    </span>
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
      <div className="space-y-3 pt-4">
        <PreviewHeading className="text-sm font-medium text-foreground">
          {t("Preview")}
        </PreviewHeading>
        <div
          className={cn(
            "grid grid-cols-2 gap-4",
            layout === "settings" ? "max-w-[30rem]" : "max-w-[41rem]",
          )}
        >
          {themes.map((theme) => (
            <figure key={theme.value} className="m-0 min-w-0 space-y-1.5">
              <figcaption className="text-xs text-muted-foreground">{t(theme.label)}</figcaption>
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
