import { useEffect, useState } from "react";
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
import { BarChart3, Boxes, Clock, Coins, Layers, Settings2, Weight } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { usePrintStatistics, useVaultConfig } from "@/lib/queries";
import type { StatsPeriod } from "@/lib/api";
import { formatDuration } from "@/lib/format";
import { formatCurrency } from "@/lib/currency";
import type {
  CollectionStatRead,
  FilamentStatRead,
  PrintStatisticsRead,
} from "@/types";

const PERIODS: { value: StatsPeriod; label: string }[] = [
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "90d", label: "90 days" },
  { value: "1y", label: "1 year" },
  { value: "all", label: "All time" },
];

const METRICS = [
  { id: "cost", label: "Cost" },
  { id: "filament", label: "Filament" },
  { id: "prints", label: "Prints" },
] as const;
type Metric = (typeof METRICS)[number]["id"];

const CHART_TYPES = [
  { id: "area", label: "Area" },
  { id: "line", label: "Line" },
  { id: "bar", label: "Bar" },
] as const;
type ChartType = (typeof CHART_TYPES)[number]["id"];

const ACCENT = "var(--chart-1)";
const CHROME = "var(--chart-grid)";
const AXIS_TICK = { fill: CHROME } as const;
const CURSOR_FILL = { fill: CHROME, fillOpacity: 0.18 } as const;
const CURSOR_LINE = { stroke: ACCENT, strokeOpacity: 0.4 } as const;

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
  if (!active || !payload || payload.length === 0) return null;
  const raw = payload[0]?.value;
  const text =
    formatValue && raw != null ? formatValue(Number(raw)) : String(raw ?? "");
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 text-xs shadow-md">
      {label != null && (
        <div className="mb-0.5 font-semibold text-foreground">{label}</div>
      )}
      <div className="text-muted-foreground">
        {valueLabel}: <span className="font-medium text-foreground">{text}</span>
      </div>
    </div>
  );
}

function formatFilament(grams: number | null | undefined): string {
  if (grams == null) return "—";
  if (grams >= 1000) return `${(grams / 1000).toFixed(2)} kg`;
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
          {opt.label}
        </button>
      ))}
    </div>
  );
}

function MetricCard({
  icon: Icon,
  label,
  value,
  tone = "blue",
}: {
  icon: typeof Coins;
  label: string;
  value: string;
  tone?: "blue" | "cyan" | "violet";
}) {
  const toneClasses = {
    blue: "bg-accent text-primary",
    cyan: "bg-chart-cyan-soft text-chart-cyan",
    violet: "bg-chart-violet-soft text-chart-violet",
  }[tone];
  return (
    <Card className="overflow-hidden">
      <CardContent className="flex items-center gap-3 p-4">
        <div className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-md ${toneClasses}`}>
          <Icon className="h-5 w-5" />
        </div>
        <div className="min-w-0">
          <div className="truncate text-xs uppercase tracking-wider text-muted-foreground">
            {label}
          </div>
          <div className="truncate text-xl font-bold text-foreground">{value}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function filamentLabel(f: FilamentStatRead): string {
  if (f.material_type && f.material_brand) return `${f.material_brand} ${f.material_type}`;
  return f.material_type || f.material_brand || "Unknown";
}

type WidgetId = "collections" | "filaments" | "models" | "printers";
const WIDGETS: { id: WidgetId; label: string }[] = [
  { id: "models", label: "Most printed models" },
  { id: "printers", label: "Printer workload" },
  { id: "filaments", label: "Filament usage" },
  { id: "collections", label: "Top collections" },
];

function RankingCard({
  title,
  data,
  valueLabel,
  tone = "blue",
  formatValue = (value) => String(value),
}: {
  title: string;
  data: { name: string; value: number }[];
  valueLabel: string;
  tone?: "blue" | "cyan" | "violet";
  formatValue?: (value: number) => string;
}) {
  const max = Math.max(...data.map((item) => item.value), 1);
  const fill = {
    blue: "bg-chart-blue",
    cyan: "bg-chart-cyan",
    violet: "bg-chart-violet",
  }[tone];
  return (
    <Card className="overflow-hidden">
      <CardHeader className="border-b border-border/70 pb-3">
        <CardTitle className="text-sm font-semibold">{title}</CardTitle>
        <p className="text-xs text-muted-foreground">Ranked by {valueLabel.toLowerCase()}</p>
      </CardHeader>
      <CardContent className="pt-4">
        {data.length === 0 ? (
          <p className="flex h-40 items-center justify-center text-sm text-muted-foreground">No data for this period</p>
        ) : (
          <ol className="space-y-3">
            {data.slice(0, 8).map((item, index) => (
              <li key={`${item.name}-${index}`} className="grid grid-cols-[1.25rem_minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
                <span className="row-span-2 font-mono text-2xs tabular-nums text-muted-foreground">{String(index + 1).padStart(2, "0")}</span>
                <span className="truncate text-xs font-medium text-foreground" title={item.name}>{item.name}</span>
                <span className="font-mono text-2xs tabular-nums text-muted-foreground">{formatValue(item.value)}</span>
                <div className="col-span-2 col-start-2 h-1.5 overflow-hidden rounded-full bg-muted">
                  <div className={`h-full origin-left rounded-full ${fill}`} style={{ transform: `scaleX(${item.value / max})` }} />
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
  currency: string;
}) {
  const [metric, setMetric] = useState<Metric>("cost");
  const [chartType, setChartType] = useState<ChartType>("area");

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
  const formatValue = (v: number) =>
    metric === "cost"
      ? formatCurrency(v, currency)
      : metric === "filament"
        ? formatFilament(v)
        : String(Math.round(v));

  const grid = (
    <CartesianGrid vertical={false} strokeDasharray="3 5" stroke={CHROME} strokeOpacity={0.32} />
  );
  const xAxis = (
    <XAxis dataKey="bucket" fontSize={11} tickLine={false} axisLine={false} tickMargin={10} tick={AXIS_TICK} tickFormatter={(value) => new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${value}T00:00:00`))} />
  );
  const yAxis = (
    <YAxis
      fontSize={11}
      tickLine={false}
      axisLine={false}
      tickMargin={8}
      width={56}
      tick={AXIS_TICK}
      tickFormatter={(v) => formatValue(Number(v))}
    />
  );
  const tooltip = (cursor: object) => (
    <Tooltip
      content={<ChartTooltip valueLabel={metricLabel} formatValue={formatValue} />}
      cursor={cursor}
    />
  );

  return (
    <Card className="overflow-hidden">
      <CardHeader className="flex flex-col gap-3 border-b border-border/70 p-5 sm:flex-row sm:items-center sm:justify-between">
        <div><CardTitle className="text-sm font-semibold">{metricLabel} over time</CardTitle><p className="mt-1 text-xs text-muted-foreground">Completed jobs in selected period</p></div>
        <div className="flex flex-wrap gap-2">
          <Segmented options={METRICS} value={metric} onChange={setMetric} />
          <Segmented options={CHART_TYPES} value={chartType} onChange={setChartType} />
        </div>
      </CardHeader>
      <CardContent className="px-5 pb-5">
        <ResponsiveContainer width="100%" height={260}>
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
              <Bar dataKey="value" fill={ACCENT} radius={[4, 4, 0, 0]} />
            </BarChart>
          )}
        </ResponsiveContainer>
      </CardContent>
    </Card>
  );
}

function StatsContent({
  stats,
  currency,
  visibleWidgets,
}: {
  stats: PrintStatisticsRead;
  currency: string;
  visibleWidgets: Set<WidgetId>;
}) {
  if (stats.total_prints === 0) {
    return (
      <Card className="animate-panel-in">
        <CardContent className="flex flex-col items-center justify-center gap-2 py-16 text-center">
          <BarChart3 className="h-8 w-8 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">
            No completed prints in this period.
          </p>
        </CardContent>
      </Card>
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
  const printerData = stats.top_printers.map((p) => ({ name: p.name, value: Math.round(p.print_time_s / 3600) }));
  const collectionRanking = collectionData.map((c) => ({ name: c.name, value: c.prints }));
  const periodDays = stats.start_at
    ? Math.max(1, (new Date(stats.end_at).getTime() - new Date(stats.start_at).getTime()) / 86400000)
    : Math.max(1, stats.cost_over_time.length);
  const weeklyFilament = stats.total_filament_g == null ? null : stats.total_filament_g / periodDays * 7;

  return (
    <div className="space-y-6 animate-panel-in">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3 xl:grid-cols-6">
        <MetricCard
          icon={Coins}
          label="Total cost"
          value={formatCurrency(stats.total_cost, currency)}
        />
        <MetricCard icon={Boxes} label="Prints" value={String(stats.total_prints)} tone="cyan" />
        <MetricCard
          icon={Weight}
          label="Filament used"
          value={formatFilament(stats.total_filament_g)}
          tone="violet"
        />
        <MetricCard
          icon={Layers}
          label="Avg / print"
          value={formatFilament(stats.avg_filament_g)}
        />
        <MetricCard
          icon={Clock}
          label="Print time"
          value={formatDuration(stats.total_print_time_s)}
        />
        <MetricCard icon={Weight} label="7-day forecast" value={formatFilament(weeklyFilament)} tone="violet" />
      </div>

      <TimeSeriesCard stats={stats} currency={currency} />

      <p className="-mt-3 text-xs text-muted-foreground">7-day forecast uses selected period’s daily filament average.</p>

      <div className="grid gap-6 lg:grid-cols-2">
        {visibleWidgets.has("models") && <RankingCard title="Most printed models" data={modelData} valueLabel="Prints" formatValue={(value) => `${value}×`} />}
        {visibleWidgets.has("printers") && <RankingCard title="Printer workload" data={printerData} valueLabel="Print hours" tone="cyan" formatValue={(value) => `${value}h`} />}
        {visibleWidgets.has("filaments") && <RankingCard title="Filament usage" data={filamentData} valueLabel="Filament weight" tone="violet" formatValue={formatFilament} />}
        {visibleWidgets.has("collections") && <RankingCard title="Top collections" data={collectionRanking} valueLabel="Prints" formatValue={(value) => `${value}×`} />}
      </div>
    </div>
  );
}

export default function StatisticsPage() {
  const [period, setPeriod] = useState<StatsPeriod>("30d");
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [visibleWidgets, setVisibleWidgets] = useState<Set<WidgetId>>(
    () => new Set(WIDGETS.map((widget) => widget.id)),
  );
  const { data, isLoading, isError } = usePrintStatistics(period);
  const { data: config } = useVaultConfig();
  const currency = config?.currency ?? "USD";

  useEffect(() => {
    const saved = window.localStorage.getItem("printstash:statistics-widgets");
    if (saved) {
      try { setVisibleWidgets(new Set(JSON.parse(saved) as WidgetId[])); } catch { /* Ignore invalid old preference. */ }
    }
  }, []);

  function toggleWidget(id: WidgetId) {
    setVisibleWidgets((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      window.localStorage.setItem("printstash:statistics-widgets", JSON.stringify([...next]));
      return next;
    });
  }

  return (
    <PageContainer>
      <PageHeader
        title="Statistics"
        description="Cost, filament and print activity from completed jobs"
        actions={<div className="flex flex-wrap items-center gap-2">
          <Segmented options={PERIODS.map((p) => ({ id: p.value, label: p.label }))} value={period} onChange={setPeriod} />
          <DropdownMenu
            open={customizeOpen}
            onOpenChange={setCustomizeOpen}
            role="dialog"
            trigger={<Button data-menu-trigger variant="outline" size="xs" aria-haspopup="dialog" aria-expanded={customizeOpen} onClick={() => setCustomizeOpen((open) => !open)}><Settings2 className="h-3.5 w-3.5" />Customize</Button>}
            contentClassName="w-64 rounded-md border border-border bg-popover p-2 text-popover-foreground shadow-md"
          >
            <p className="px-2 pb-2 text-xs font-semibold">Visible ranking charts</p>
            {WIDGETS.map((widget) => <label key={widget.id} className="flex cursor-pointer items-center gap-2 rounded px-2 py-2 text-sm hover:bg-popover-hover"><input type="checkbox" checked={visibleWidgets.has(widget.id)} onChange={() => toggleWidget(widget.id)} className="h-4 w-4 accent-primary" />{widget.label}</label>)}
          </DropdownMenu>
        </div>}
      />

      {isLoading && (
        <div className="animate-panel-in py-16 text-center text-sm text-muted-foreground">
          Loading statistics…
        </div>
      )}
      {isError && (
        <Card className="animate-panel-in">
          <CardContent className="py-12 text-center text-sm text-destructive">
            Failed to load statistics.
          </CardContent>
        </Card>
      )}
      {data && <StatsContent stats={data} currency={currency} visibleWidgets={visibleWidgets} />}
    </PageContainer>
  );
}
