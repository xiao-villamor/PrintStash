import { ApiError } from "@/lib/errors";
import { useAuth } from "@/lib/auth-context";
import { formatNumber } from "@/lib/format";
import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { useCallback, useState } from "react";
import { Link } from "@/lib/link";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BarChart3, Settings2 } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { ErrorState } from "@/components/ui/error-state";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { TabBar } from "@/components/ui/tabs";
import { inputClasses } from "@/components/ui/input";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { usePrintStatistics, useVaultConfig } from "@/lib/queries";
import type { StatsPeriod } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { formatCurrency } from "@/lib/currency";
import { useI18n } from "@/lib/i18n";
import { translateUiText } from "@/lib/locale";
import type { CollectionStatRead, FilamentStatRead, PrintStatisticsRead } from "@/types";

const PERIODS: { value: StatsPeriod; label: string }[] = [
  {
    value: "7d",
    get label() {
      return uiText("7 days");
    },
  },
  {
    value: "30d",
    get label() {
      return uiText("30 days");
    },
  },
  {
    value: "90d",
    get label() {
      return uiText("90 days");
    },
  },
  {
    value: "1y",
    get label() {
      return uiText("1 year");
    },
  },
  {
    value: "all",
    get label() {
      return uiText("All time");
    },
  },
];

const METRICS = [
  {
    id: "cost",
    get label() {
      return uiText("Cost");
    },
  },
  {
    id: "filament",
    get label() {
      return uiText("Filament");
    },
  },
  {
    id: "prints",
    get label() {
      return uiText("Prints");
    },
  },
] as const;
type Metric = (typeof METRICS)[number]["id"];

const CHART_TYPES = [
  {
    id: "area",
    get label() {
      return uiText("Area");
    },
  },
  {
    id: "line",
    get label() {
      return uiText("Line");
    },
  },
  {
    id: "bar",
    get label() {
      return uiText("Bar");
    },
  },
] as const;
type ChartType = (typeof CHART_TYPES)[number]["id"];

const ACCENT = "var(--chart-1)";
const CHROME = "var(--chart-grid)";
const AXIS_TICK = { fill: "var(--muted-foreground)" } as const;
const CURSOR_FILL = { fill: CHROME, fillOpacity: 0.18 } as const;
const CURSOR_LINE = { stroke: ACCENT, strokeOpacity: 0.4 } as const;

/** SVG presentation attributes recharts draws the tooltip cursor with. */
type ChartCursor = typeof CURSOR_FILL | typeof CURSOR_LINE;

function ChartTooltip({
  active,
  payload,
  label,
  valueLabel,
  formatValue,
}: {
  active?: boolean;
  payload?: { value?: number | string }[];
  label?: string | number;
  valueLabel: string;
  formatValue?: (v: number) => string;
}) {
  useUiLocale();
  const { locale } = useI18n();
  if (!active || !payload || payload.length === 0) return null;
  const raw = payload[0]?.value;
  const text = formatValue && raw != null ? formatValue(Number(raw)) : String(raw ?? "");
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      {label != null && <div className="mb-0.5 font-semibold text-foreground">{label}</div>}
      <div className="text-muted-foreground">
        {translateUiText(locale, valueLabel)}:{" "}
        <span className="font-medium text-foreground">{text}</span>
      </div>
    </div>
  );
}

function formatFilament(grams: number | null | undefined): string {
  if (grams == null) return "—";
  if (grams >= 1000)
    return `${formatNumber(grams / 1000, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} kg`;
  return `${Math.round(grams)} g`;
}

function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly { id: T; label: string }[];
  value: T;
  onChange: (v: T) => void;
}) {
  useUiLocale();
  const { locale } = useI18n();
  return (
    <div className="inline-flex shrink-0 rounded-md border border-border bg-card p-0.5">
      {options.map((opt) => (
        <button
          key={opt.id}
          type="button"
          onClick={() => onChange(opt.id)}
          aria-pressed={value === opt.id}
          className={`rounded px-2.5 py-1 text-xs font-medium transition-[color,background-color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset ${
            value === opt.id
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground"
          }`}
        >
          {translateUiText(locale, opt.label)}
        </button>
      ))}
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  useUiLocale();
  const { locale } = useI18n();
  return (
    <div className="min-w-0 bg-card p-4">
      <div className="text-xs text-muted-foreground">{translateUiText(locale, label)}</div>
      <div className="mt-1 break-words text-xl font-semibold tabular-nums text-foreground">
        {value}
      </div>
    </div>
  );
}

function PeriodSelector({
  period,
  onChange,
}: {
  period: StatsPeriod;
  onChange: (period: StatsPeriod) => void;
}) {
  const { t } = useI18n();
  return (
    <label className="inline-flex items-center gap-2 text-xs text-foreground">
      {t("Period")}
      <select
        value={period}
        onChange={(event) => {
          const selected = PERIODS.find((option) => option.value === event.target.value);
          if (selected) onChange(selected.value);
        }}
        className={`${inputClasses} h-9 w-auto`}
      >
        {PERIODS.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function filamentLabel(f: Pick<FilamentStatRead, "material_type" | "material_brand">): string {
  if (f.material_type && f.material_brand) return `${f.material_brand} ${f.material_type}`;
  return f.material_type || f.material_brand || uiText("Unknown");
}

type RankingId = "collections" | "filaments" | "models" | "printers";
const PANEL_IDS = ["outcomes", "failures", "coverage", "printer-filaments"] as const;
type PanelId = (typeof PANEL_IDS)[number];
type WidgetId = RankingId | PanelId;
const WIDGETS: { id: WidgetId; label: string }[] = [
  {
    id: "models",
    get label() {
      return uiText("Most printed models");
    },
  },
  {
    id: "printers",
    get label() {
      return uiText("Printer workload");
    },
  },
  {
    id: "filaments",
    get label() {
      return uiText("Filament usage");
    },
  },
  {
    id: "collections",
    get label() {
      return uiText("Top collections");
    },
  },
  {
    id: "outcomes",
    get label() {
      return uiText("Print outcomes");
    },
  },
  {
    id: "failures",
    get label() {
      return uiText("Failures by printer");
    },
  },
  {
    id: "coverage",
    get label() {
      return uiText("Print data coverage");
    },
  },
  {
    id: "printer-filaments",
    get label() {
      return uiText("Filament by printer");
    },
  },
];
const ALL_WIDGET_IDS: readonly WidgetId[] = WIDGETS.map((widget) => widget.id);
const DEFAULT_WIDGETS: readonly RankingId[] = ["models", "printers", "filaments", "collections"];
const WIDGET_PREFERENCE_KEY = "printstash:statistics-widgets";

/**
 * Parses the saved widget preference into the set of widgets to show.
 *
 * The stored value is whatever a previous build wrote, so it is treated as
 * untrusted: anything that is not a JSON array of ids we still ship is
 * discarded and the default ranking panels are shown, which is also the first-visit default.
 */
function loadVisibleWidgets(): Set<WidgetId> {
  const saved = window.localStorage.getItem(WIDGET_PREFERENCE_KEY);
  if (saved === null) return new Set(DEFAULT_WIDGETS);
  try {
    const parsed: unknown = JSON.parse(saved);
    if (!Array.isArray(parsed)) return new Set(DEFAULT_WIDGETS);
    return new Set(ALL_WIDGET_IDS.filter((id) => parsed.includes(id)));
  } catch {
    return new Set(DEFAULT_WIDGETS);
  }
}

function RankingCard({
  title,
  data,
  valueLabel,
  tone = "blue",
  formatValue = (value) => String(value),
}: {
  title: string;
  data: { name: string; value: number; detail?: string }[];
  valueLabel: string;
  tone?: "blue" | "cyan" | "violet";
  formatValue?: (value: number) => string;
}) {
  useUiLocale();
  const { locale } = useI18n();
  const max = Math.max(...data.map((item) => item.value), 1);
  const localizedTitle = translateUiText(locale, title);
  const localizedValueLabel = translateUiText(locale, valueLabel);
  const fill = {
    blue: "bg-chart-blue",
    cyan: "bg-chart-cyan",
    violet: "bg-chart-violet",
  }[tone];
  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b border-border/70 pb-3">
        <CardTitle className="text-sm font-semibold">{localizedTitle}</CardTitle>
        <p className="text-xs text-muted-foreground">
          {uiText("Ranked by {metric}", { metric: localizedValueLabel })}
        </p>
      </CardHeader>
      <CardContent className="pt-4">
        {data.length === 0 ? (
          <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">
            {translateUiText(locale, "No data for this period")}
          </p>
        ) : (
          <ol className="space-y-3">
            {data.slice(0, 8).map((item, index) => (
              <li
                key={`${item.name}-${index}`}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1"
              >
                <span className="truncate text-xs font-medium text-foreground" title={item.name}>
                  {item.name}
                </span>
                <span className="font-mono text-2xs tabular-nums text-muted-foreground">
                  {formatValue(item.value)}
                </span>
                {item.detail && (
                  <span className="col-span-2 text-xs text-muted-foreground">{item.detail}</span>
                )}
                <div className="col-span-2 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div
                    className={`h-full origin-left rounded-full ${fill}`}
                    style={{ transform: `scaleX(${item.value / max})` }}
                  />
                </div>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function TimeSeriesCard({
  stats,
  currency,
}: {
  stats: PrintStatisticsRead;
  currency: string | null;
}) {
  useUiLocale();
  const { locale, t } = useI18n();
  const [metric, setMetric] = useState<Metric>("prints");
  const [chartType, setChartType] = useState<ChartType>("bar");

  const data = stats.cost_over_time.map((b) => ({
    bucket: b.bucket,
    value:
      metric === "cost"
        ? (b.cost ?? 0)
        : metric === "filament"
          ? (b.filament_g ?? 0)
          : b.print_count,
  }));

  const metricLabel = METRICS.find((m) => m.id === metric)!.label;
  const localizedMetricLabel = translateUiText(locale, metricLabel);
  const formatValue = (v: number) =>
    metric === "cost"
      ? currency === null
        ? t("stats.costUnavailable")
        : formatCurrency(v, currency)
      : metric === "filament"
        ? formatFilament(v)
        : String(Math.round(v));

  const grid = <CartesianGrid vertical={false} stroke={CHROME} strokeOpacity={0.65} />;
  const xAxis = (
    <XAxis
      dataKey="bucket"
      fontSize={11}
      tickLine={false}
      axisLine={false}
      tickMargin={10}
      tick={AXIS_TICK}
      tickFormatter={(value: string) => {
        const monthly = value.length === 7;
        return new Intl.DateTimeFormat(currentLocale(), {
          month: "short",
          ...(monthly ? { year: "numeric" } : { day: "numeric" }),
        }).format(new Date(`${monthly ? `${value}-01` : value}T00:00:00`));
      }}
    />
  );
  const yAxis = (
    <YAxis
      fontSize={11}
      tickLine={false}
      axisLine={false}
      tickMargin={8}
      width={56}
      allowDecimals={metric !== "prints"}
      tick={AXIS_TICK}
      tickFormatter={(v) => formatValue(Number(v))}
    />
  );
  const tooltip = (cursor: ChartCursor) => (
    <Tooltip
      content={<ChartTooltip valueLabel={localizedMetricLabel} formatValue={formatValue} />}
      cursor={cursor}
    />
  );

  return (
    <Card className="min-w-0 overflow-hidden">
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0 border-b p-4">
        <CardTitle className="text-sm font-semibold">
          {metric === "prints"
            ? t("Completed jobs in selected period")
            : uiText("{metric} over time", { metric: localizedMetricLabel })}
        </CardTitle>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex items-center gap-2 text-xs text-foreground">
            {t("Metric")}
            <select
              value={metric}
              onChange={(event) => {
                const selected = METRICS.find((option) => option.id === event.target.value);
                if (selected) setMetric(selected.id);
              }}
              className={`${inputClasses} h-9 w-auto`}
            >
              {METRICS.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </label>
          <Segmented options={CHART_TYPES} value={chartType} onChange={setChartType} />
        </div>
      </CardHeader>
      <CardContent className="px-3 pb-3 pt-4 sm:px-4">
        {metric === "cost" && currency === null ? (
          <p role="status" className="py-12 text-center text-sm text-muted-foreground">
            {t("stats.costUnavailable")}
          </p>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            {chartType === "area" ? (
              <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="metricFill" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor={ACCENT} stopOpacity={0.3} />
                    <stop offset="100%" stopColor={ACCENT} stopOpacity={0} />
                  </linearGradient>
                </defs>
                {grid}
                {xAxis}
                {yAxis}
                {tooltip(CURSOR_LINE)}
                <Area
                  isAnimationActive={false}
                  type="monotone"
                  dataKey="value"
                  stroke={ACCENT}
                  strokeWidth={2}
                  fill="url(#metricFill)"
                  activeDot={{ r: 4, fill: "var(--card)", stroke: ACCENT, strokeWidth: 2 }}
                />
              </AreaChart>
            ) : chartType === "line" ? (
              <LineChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                {grid}
                {xAxis}
                {yAxis}
                {tooltip(CURSOR_LINE)}
                <Line
                  isAnimationActive={false}
                  type="monotone"
                  dataKey="value"
                  stroke={ACCENT}
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, fill: "var(--card)", stroke: ACCENT, strokeWidth: 2 }}
                />
              </LineChart>
            ) : (
              <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
                {grid}
                {xAxis}
                {yAxis}
                {tooltip(CURSOR_FILL)}
                <Bar dataKey="value" fill={ACCENT} maxBarSize={28} isAnimationActive={false} />
              </BarChart>
            )}
          </ResponsiveContainer>
        )}
      </CardContent>
    </Card>
  );
}

function OutcomePanels({ stats, visible }: { stats: PrintStatisticsRead; visible: Set<WidgetId> }) {
  const { failed, cancelled } = stats.outcomes;
  const completed = stats.total_prints;
  const decided = completed + failed;
  const settled = decided + cancelled;
  const coverage = stats.data_coverage;
  const outcomes = [
    { label: uiText("Completed"), count: completed, color: "bg-chart-blue" },
    { label: uiText("Failed"), count: failed, color: "bg-chart-violet" },
    { label: uiText("Cancelled"), count: cancelled, color: "bg-chart-cyan" },
  ];
  const sources = [
    [uiText("Recorded print time"), coverage.recorded_time],
    [uiText("Slicer time estimate"), coverage.slicer_time],
    [uiText("Recorded filament weight"), coverage.recorded_filament],
    [uiText("Slicer filament estimate"), coverage.slicer_filament],
    [uiText("Known filament cost"), coverage.known_cost],
  ] as const;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {visible.has("outcomes") && (
        <Card className="min-w-0">
          <CardHeader className="border-b p-4">
            <CardTitle className="text-sm">{uiText("Print outcomes")}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4 p-4">
            <dl className="grid grid-cols-2 gap-3">
              <div>
                <dt className="text-xs text-muted-foreground">{uiText("Success rate")}</dt>
                <dd className="text-2xl font-semibold tabular-nums">
                  {decided > 0
                    ? formatNumber(completed / decided, {
                        style: "percent",
                        maximumFractionDigits: 1,
                      })
                    : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-muted-foreground">{uiText("Finished jobs")}</dt>
                <dd className="text-2xl font-semibold tabular-nums">{formatNumber(settled)}</dd>
              </div>
            </dl>
            <div className="flex h-2 overflow-hidden bg-muted" aria-hidden="true">
              {outcomes
                .filter(({ count }) => count > 0)
                .map(({ label, color, count }) => (
                  <div key={label} className={color} style={{ flex: count }} />
                ))}
            </div>
            <dl className="grid grid-cols-3 gap-2 text-xs">
              {outcomes.map(({ label, color, count }) => (
                <div key={label} className="min-w-0">
                  <dt className="flex items-center gap-1.5 break-words text-muted-foreground">
                    <span className={`size-2 shrink-0 ${color}`} aria-hidden="true" />
                    {label}
                  </dt>
                  <dd className="mt-1 font-mono tabular-nums">{formatNumber(count)}</dd>
                </div>
              ))}
            </dl>
            <p className="text-xs text-muted-foreground">
              {uiText("Success rate uses completed and failed jobs.")}
            </p>
          </CardContent>
        </Card>
      )}
      {visible.has("failures") && (
        <RankingCard
          title={uiText("Failures by printer")}
          valueLabel={uiText("Failed jobs")}
          tone="violet"
          data={stats.printer_failures.map((printer) => ({
            name: printer.name ?? uiText("Unassigned / manual"),
            value: printer.failed,
            detail: uiText("{count} completed", { count: formatNumber(printer.completed) }),
          }))}
          formatValue={formatNumber}
        />
      )}
      {visible.has("coverage") && (
        <Card className="min-w-0">
          <CardHeader className="border-b p-4">
            <CardTitle className="text-sm">{uiText("Print data coverage")}</CardTitle>
          </CardHeader>
          <CardContent className="p-4">
            <dl className="grid gap-3 text-xs">
              {sources.map(([label, count]) => (
                <div key={label} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2">
                  <dt>{label}</dt>
                  <dd className="font-mono tabular-nums">
                    {uiText("{known} of {total}", {
                      known: formatNumber(count),
                      total: formatNumber(completed),
                    })}
                  </dd>
                  <dd aria-hidden="true" className="col-span-2 h-1.5 overflow-hidden bg-muted">
                    <span
                      className="block h-full origin-left bg-chart-cyan rtl:origin-right"
                      style={{ transform: `scaleX(${completed > 0 ? count / completed : 0})` }}
                    />
                  </dd>
                </div>
              ))}
            </dl>
          </CardContent>
        </Card>
      )}
      {visible.has("printer-filaments") && (
        <RankingCard
          title={uiText("Filament by printer")}
          valueLabel={uiText("Filament weight")}
          data={stats.printer_filaments.map((row) => ({
            name: filamentLabel(row),
            value: row.total_g,
            detail: row.printer_name ?? uiText("Unassigned / manual"),
          }))}
          formatValue={formatFilament}
          tone="cyan"
        />
      )}
    </div>
  );
}

function StatsContent({
  stats,
  currency,
  visibleWidgets,
  section,
}: {
  stats: PrintStatisticsRead;
  currency: string | null;
  visibleWidgets: Set<WidgetId>;
  section: "overview" | "rankings";
}) {
  useUiLocale();
  const { locale, t } = useI18n();
  const panels =
    section === "overview" && PANEL_IDS.some((id) => visibleWidgets.has(id)) ? (
      <OutcomePanels stats={stats} visible={visibleWidgets} />
    ) : null;
  if (stats.total_prints === 0) {
    return (
      <div className="space-y-4">
        {panels}
        <Card className="animate-panel-in">
          <CardContent className="flex flex-col items-center justify-center gap-2 py-16 text-center">
            <BarChart3 className="h-8 w-8 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              {translateUiText(locale, "No completed prints in this period.")}
            </p>
            <p className="max-w-md text-sm text-muted-foreground">
              {translateUiText(
                locale,
                "Material and time totals use completed jobs. Review the selected period or manage your printers.",
              )}
            </p>
            <Button variant="outline" className="mt-2" asChild>
              <Link href="/printers">{translateUiText(locale, "Manage printers")}</Link>
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  const collectionData = stats.top_collections.map((c: CollectionStatRead) => ({
    name: c.name,
    prints: c.print_count,
  }));
  const filamentData = stats.top_filaments.map((f) => ({
    name: filamentLabel(f),
    value: f.total_g ?? 0,
  }));
  const modelData = stats.top_models.map((m) => ({ name: m.name, value: m.print_count }));
  const printerData = stats.top_printers.map((p) => ({
    name: p.name,
    value: p.print_time_s,
  }));
  const collectionRanking = collectionData.map((c) => ({ name: c.name, value: c.prints }));
  const periodDays = stats.start_at
    ? Math.max(
        1,
        (new Date(stats.end_at).getTime() - new Date(stats.start_at).getTime()) / 86400000,
      )
    : Math.max(1, stats.cost_over_time.length);
  const weeklyFilament =
    stats.total_filament_g == null ? null : (stats.total_filament_g / periodDays) * 7;

  const rankingCards = [
    {
      id: "models",
      card: (
        <RankingCard
          title={uiText("Most printed models")}
          data={modelData}
          valueLabel={uiText("Prints")}
          formatValue={(value) => `${value}×`}
        />
      ),
    },
    {
      id: "printers",
      card: (
        <RankingCard
          title={uiText("Printer workload")}
          data={printerData}
          valueLabel={uiText("Print time")}
          tone="cyan"
          formatValue={formatDuration}
        />
      ),
    },
    {
      id: "filaments",
      card: (
        <RankingCard
          title={uiText("Filament usage")}
          data={filamentData}
          valueLabel={uiText("Filament weight")}
          tone="violet"
          formatValue={formatFilament}
        />
      ),
    },
    {
      id: "collections",
      card: (
        <RankingCard
          title={uiText("Top collections")}
          data={collectionRanking}
          valueLabel={uiText("Prints")}
          formatValue={(value) => `${value}×`}
        />
      ),
    },
  ] satisfies { id: RankingId; card: React.ReactNode }[];
  const rankings = rankingCards.filter(({ id }) => visibleWidgets.has(id));
  const [previewRanking, ...remainingRankings] = rankings;

  return (
    <div className="space-y-6 animate-panel-in">
      <div hidden={section !== "overview"} className="space-y-4">
        <div className="grid grid-cols-2 gap-px overflow-hidden border bg-border sm:grid-cols-3 2xl:grid-cols-6">
          <MetricCard
            label={uiText("Total cost")}
            value={
              currency === null
                ? t("stats.costUnavailable")
                : formatCurrency(stats.total_cost, currency)
            }
          />
          <MetricCard
            label={uiText("Print time")}
            value={formatDuration(stats.total_print_time_s)}
          />
          <MetricCard
            label={uiText("Filament used")}
            value={formatFilament(stats.total_filament_g)}
          />
          <MetricCard label={uiText("Completed prints")} value={formatNumber(stats.total_prints)} />
          <MetricCard label={uiText("Avg / print")} value={formatFilament(stats.avg_filament_g)} />
          <MetricCard label={uiText("7-day forecast")} value={formatFilament(weeklyFilament)} />
        </div>

        <p className="text-xs text-muted-foreground">
          {translateUiText(locale, "7-day forecast uses selected period’s daily filament average.")}
        </p>

        <div
          className={
            previewRanking ? "grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]" : undefined
          }
        >
          <TimeSeriesCard stats={stats} currency={currency} />
          {section === "overview" && previewRanking?.card}
        </div>
      </div>

      {panels}
      <div
        className={
          section === "rankings"
            ? "grid gap-4 lg:grid-cols-2"
            : "grid gap-4 lg:grid-cols-2 2xl:grid-cols-3"
        }
      >
        {(section === "rankings" ? rankings : remainingRankings).map(({ id, card }) => (
          <div key={id} className="min-w-0">
            {card}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function StatisticsPage() {
  useUiLocale();
  const { locale, t } = useI18n();
  const { user } = useAuth();
  const [period, setPeriod] = useState<StatsPeriod>("30d");
  const [section, setSection] = useState<"overview" | "rankings">("overview");
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const focusFirstWidget = useCallback((button: HTMLButtonElement | null) => {
    button?.focus({ preventScroll: true });
  }, []);
  const [visibleWidgets, setVisibleWidgets] = useState<Set<WidgetId>>(loadVisibleWidgets);
  const { data, error, isLoading, isError, refetch } = usePrintStatistics(period);
  const denied = error instanceof ApiError && [401, 403, 404].includes(error.status);
  const config = useVaultConfig({ enabled: !!user?.is_superuser, retry: false });
  const currency = config.isError ? null : (config.data?.currency ?? null);

  function toggleWidget(id: WidgetId) {
    setVisibleWidgets((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      window.localStorage.setItem(WIDGET_PREFERENCE_KEY, JSON.stringify([...next]));
      return next;
    });
  }

  return (
    <PageContainer>
      <PageHeader
        title={translateUiText(locale, "Statistics")}
        description={translateUiText(
          locale,
          "Cost, filament and print activity from completed jobs",
        )}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <PeriodSelector period={period} onChange={setPeriod} />
            <DropdownMenu
              portal
              open={customizeOpen}
              onOpenChange={setCustomizeOpen}
              role="dialog"
              aria-label={uiText("Visible statistics panels")}
              trigger={
                <Button
                  data-menu-trigger
                  variant="outline"
                  size="xs"
                  aria-haspopup="dialog"
                  aria-expanded={customizeOpen}
                  onClick={() => setCustomizeOpen((open) => !open)}
                >
                  <Settings2 className="h-3.5 w-3.5" />
                  {translateUiText(locale, "Customize")}
                </Button>
              }
              contentClassName="w-64 rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-md"
            >
              <p className="px-2 pb-2 text-xs font-semibold">
                {uiText("Visible statistics panels")}
              </p>
              {WIDGETS.map((widget, index) => (
                <label
                  key={widget.id}
                  className="flex cursor-action items-center gap-2 rounded px-2 py-2 text-sm hover:bg-popover-hover"
                >
                  <Checkbox
                    ref={customizeOpen && index === 0 ? focusFirstWidget : undefined}
                    checked={visibleWidgets.has(widget.id)}
                    onChange={() => toggleWidget(widget.id)}
                    className="shrink-0"
                  />
                  {translateUiText(locale, widget.label)}
                </label>
              ))}
            </DropdownMenu>
          </div>
        }
      />

      <TabBar
        variant="framed"
        tabs={[
          { key: "overview", label: t("Overview") },
          { key: "rankings", label: t("Rankings") },
        ]}
        active={section}
        onChange={setSection}
      />

      {user?.is_superuser && config.isError && (
        <ErrorState
          message={t("settings.configLoadFailed")}
          retryLabel={t("Retry")}
          onRetry={() => void config.refetch()}
        />
      )}
      {isLoading && (
        <div className="animate-panel-in py-16 text-center text-sm text-muted-foreground">
          {translateUiText(locale, "Loading statistics…")}
        </div>
      )}
      {isError && (
        <Card className="animate-panel-in">
          <ErrorState
            layout="block"
            message={translateUiText(locale, "Failed to load statistics.")}
            retryLabel={uiText("Retry")}
            onRetry={() => void refetch()}
          />
        </Card>
      )}
      {data && !denied && (
        <StatsContent
          stats={data}
          currency={currency}
          visibleWidgets={visibleWidgets}
          section={section}
        />
      )}
    </PageContainer>
  );
}
