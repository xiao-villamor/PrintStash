import { Monitor, Moon, Sun } from "lucide-react";
import { useId } from "react";
import metricBracket from "@/assets/metric-bracket.webp";
import { SettingsSwitch } from "@/components/settings-card";
import { SETTINGS_FIELD } from "@/components/settings-controls";
import { formatCalendarDate, formatDuration, formatNumber } from "@/lib/format";
import { RevisionStatusBadge } from "@/components/revision-status-badge";
import "./library-items.css";
import { cn } from "@/lib/utils";
import { uiText } from "@/lib/locale";
import { CARD_METRIC_OPTIONS, type CardMetricId, type CardMetrics } from "@/lib/card-metrics";

type ThemeChoice = "light" | "dark" | "system";

function ThemeLayer({ theme, half = false }: { theme: "light" | "dark"; half?: boolean }) {
  return (
    <span className="settings-theme-layer" data-theme={theme} data-clip={half ? "half" : undefined}>
      <span className="settings-theme-titlebar">
        <i />
        <i />
        <i />
      </span>
      <span className="settings-theme-body">
        <span className="settings-theme-side" />
        <span className="settings-theme-main">
          <span />
          <span />
          <span />
        </span>
      </span>
    </span>
  );
}

/** Theme as three small pictures of the interface, so the choice shows what it does. */
export function ThemeTiles({
  value,
  onChange,
}: {
  value: ThemeChoice;
  onChange: (value: ThemeChoice) => void;
}) {
  const tiles = [
    { value: "light", label: uiText("Light"), Icon: Sun },
    { value: "dark", label: uiText("Dark"), Icon: Moon },
    { value: "system", label: uiText("System"), Icon: Monitor },
  ] as const;
  return (
    <div
      role="radiogroup"
      aria-label={uiText("Theme")}
      className="grid grid-cols-[repeat(3,minmax(0,13rem))] gap-4"
    >
      {tiles.map(({ value: tile, label, Icon }) => {
        const selected = tile === value;
        return (
          <button
            key={tile}
            type="button"
            role="radio"
            aria-checked={selected}
            onClick={() => onChange(tile)}
            className="group min-w-0 text-left focus-visible:outline-none"
          >
            <span
              className={cn(
                "settings-theme-swatch block aspect-16/10 overflow-hidden rounded-lg border-2",
                selected ? "border-primary" : "border-border",
                "group-focus-visible:ring-2 group-focus-visible:ring-ring",
              )}
            >
              {tile === "system" ? (
                <>
                  <ThemeLayer theme="dark" />
                  <ThemeLayer theme="light" half />
                </>
              ) : (
                <ThemeLayer theme={tile} />
              )}
            </span>
            <span className="mt-2 flex items-center gap-2 text-sm font-medium">
              <span
                aria-hidden
                className={cn(
                  "flex size-4 shrink-0 items-center justify-center rounded-full border",
                  selected ? "border-primary" : "border-border",
                )}
              >
                {selected && <span className="size-2 rounded-full bg-primary" />}
              </span>
              <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
              {label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

/** Edit the metrics where their sample values appear on the card. */
export function CardMetricsEditor({
  metrics,
  onChange,
  showPreviewLabel,
  onShowPreviewLabelChange,
}: {
  metrics: CardMetrics;
  showPreviewLabel: boolean;
  onShowPreviewLabelChange: (checked: boolean) => void;
  onChange: (slot: 0 | 1 | 2, metric: CardMetricId) => void;
}) {
  const id = useId();
  const sampleValues = {
    layer_height: `${formatNumber(0.2, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} mm`,
    print_time: formatDuration(84 * 60),
    filament_weight: `${formatNumber(32)} g`,
    material: "PLA",
    slicer: "OrcaSlicer",
    file_count: formatNumber(3),
  } satisfies Record<CardMetricId, string>;
  const sampleName = uiText("Example bracket");
  const sampleDate = "2026-01-01T12:00:00Z";
  return (
    <article
      aria-label={sampleName}
      className="library-model-card flex w-full max-w-80 flex-col overflow-hidden rounded-lg border border-border bg-card text-card-foreground"
    >
      <div className="library-preview relative shrink-0 overflow-hidden">
        <img
          src={metricBracket}
          alt=""
          width={320}
          height={240}
          className="library-card-thumbnail"
        />
        {showPreviewLabel && (
          <span className="absolute bottom-1.5 left-1.5 rounded-md border border-input bg-card px-1.5 py-1 text-2xs text-card-foreground shadow-md">
            {uiText("modelPreview.cover")}
          </span>
        )}
      </div>
      <div className="library-card-content library-card-standard">
        <h3>
          <span>{sampleName}</span>
        </h3>
      </div>
      <div className="library-card-summary-container">
        <div className="library-card-revision-summary">
          <div className="library-card-revision-line">
            <RevisionStatusBadge status="needs_test" />
            <strong>{uiText("Rev {value1}", { value1: "1" })}</strong>
          </div>
          <div className="library-card-data-row">
            <div className="flex min-w-0 items-center gap-1">
              {([0, 1, 2] as const).map((slot) => {
                const metric = metrics[slot];
                const sampleId = `${id}-${slot}`;
                const label = CARD_METRIC_OPTIONS.find(
                  ({ id: optionId }) => optionId === metric,
                )?.label;
                return (
                  <div key={slot} className="relative flex min-w-0 items-center">
                    <select
                      aria-label={uiText("Metric {value1}", { value1: String(slot + 1) })}
                      aria-describedby={sampleId}
                      title={`${label} · ${sampleValues[metric]}`}
                      value={metric}
                      onChange={(event) => {
                        const option = CARD_METRIC_OPTIONS.find(
                          ({ id: optionId }) => optionId === event.target.value,
                        );
                        if (option) onChange(slot, option.id);
                      }}
                      className={cn(SETTINGS_FIELD, "settings-metric-field absolute inset-0")}
                    >
                      {CARD_METRIC_OPTIONS.map((option) => (
                        <option
                          key={option.id}
                          value={option.id}
                          disabled={metrics.some(
                            (value, index) => index !== slot && value === option.id,
                          )}
                        >
                          {option.label}
                        </option>
                      ))}
                    </select>
                    <span
                      id={sampleId}
                      className="pointer-events-none relative block h-6 truncate ps-1.5 pe-5 leading-6"
                    >
                      {sampleValues[metric]}
                    </span>
                  </div>
                );
              })}
            </div>
            <time dateTime={sampleDate}>{formatCalendarDate(sampleDate)}</time>
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-3 py-2 text-sm">
        <span>{uiText("Show preview label")}</span>
        <SettingsSwitch
          label={uiText("Show preview label")}
          checked={showPreviewLabel}
          onChange={onShowPreviewLabelChange}
        />
      </div>
    </article>
  );
}
