import { useId, useState, type CSSProperties, type ReactNode } from "react";
import { Check } from "lucide-react";
import { ModelCard } from "@/components/model-card";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useI18n } from "@/lib/i18n";
import { PREVIEW_LOOK_COLOURS } from "@/lib/thumbnail-profile.generated";
import type { PreviewAppearance as Appearance, PreviewLook } from "@/types/config";
import type { ModelListItem } from "@/types/models";
import { NO_PREVIEWS } from "@/types/previews";

const looks = [
  { value: "sky", label: "Sky" },
  { value: "printstash_blue", label: "PrintStash blue" },
  { value: "slate", label: "Slate" },
  { value: "graphite", label: "Graphite" },
  { value: "sand", label: "Sand" },
] as const satisfies readonly { value: PreviewLook; label: string }[];

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
  disabled,
  footer,
}: {
  value: Appearance;
  onChange: (value: Appearance) => void;
  disabled?: boolean;
  footer?: ReactNode;
}) {
  const { t } = useI18n();
  const radioName = useId();
  const [theme, setTheme] = useState<"light" | "dark">("light");
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
    <div className="grid max-w-4xl gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(14rem,17rem)]">
      <div className="min-w-0 space-y-5">
        <fieldset disabled={disabled} className="min-w-0">
          <legend className="mb-3 text-sm font-medium text-foreground">
            {t("Preview colour")}
          </legend>
          <div className="grid max-w-96 grid-cols-5 gap-3">
            {looks.map((look) => (
              <label
                key={look.value}
                className="relative flex min-w-0 cursor-pointer flex-col gap-2"
              >
                <input
                  type="radio"
                  name={radioName}
                  className="peer sr-only"
                  checked={value.preview_look === look.value}
                  onChange={() => onChange({ ...value, preview_look: look.value })}
                />
                <span
                  style={swatchStyle(look.value)}
                  className="flex aspect-square w-full items-center justify-center border border-border-strong bg-preview-swatch transition-shadow duration-press peer-checked:ring-2 peer-checked:ring-ring peer-checked:ring-offset-2 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-4 peer-focus-visible:outline-ring peer-disabled:opacity-60"
                  aria-hidden
                >
                  {value.preview_look === look.value && (
                    <span className="grid size-6 place-items-center bg-card text-card-foreground">
                      <Check className="size-4" />
                    </span>
                  )}
                </span>
                <span className="text-xs leading-tight text-muted-foreground peer-checked:font-medium peer-checked:text-foreground">
                  {t(look.label)}
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <div className="flex items-center justify-between gap-4 border-t border-border pt-4">
          <span className="text-sm font-medium text-foreground">{t("Floor grid")}</span>
          <Switch
            label={t("Floor grid")}
            checked={value.preview_floor_grid}
            onChange={(preview_floor_grid) => onChange({ ...value, preview_floor_grid })}
            stateLabels={{ on: t("On"), off: t("Off") }}
            disabled={disabled}
          />
        </div>
        {footer}
      </div>
      <figure className="m-0 min-w-0">
        <figcaption className="mb-2 flex items-center justify-between gap-2">
          <span className="text-sm font-medium text-foreground">{t("Example card")}</span>
          <div
            role="group"
            aria-label={t("Example theme")}
            className="inline-flex border border-border"
          >
            {themes.map((option) => (
              <Button
                key={option.value}
                type="button"
                size="xs"
                variant={theme === option.value ? "secondary" : "ghost"}
                aria-pressed={theme === option.value}
                onClick={() => setTheme(option.value)}
              >
                {t(option.label)}
              </Button>
            ))}
          </div>
        </figcaption>
        <div
          data-preview-theme={theme}
          className="border border-border bg-background p-3"
          role="img"
          aria-label={sampleName}
        >
          <div inert>
            <ModelCard model={sample} />
          </div>
        </div>
      </figure>
    </div>
  );
}
