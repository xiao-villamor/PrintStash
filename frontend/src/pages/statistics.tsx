import { useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { BarChart3, Boxes, Clock, Coins, Layers, Weight } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageContainer } from "@/components/ui/page-container";
import { PageHeader } from "@/components/ui/page-header";
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

// Shared accent — the app's light-mode blue, legible on both themes.
const ACCENT = "#3b82f6";
const BAR_COLORS = ["#3b82f6", "#8b5cf6", "#ec4899", "#f59e0b", "#10b981"];

// Recharts renders axes/cursor as SVG attributes, where `var(--…)` does NOT
// resolve (it falls back to black — invisible on dark). So chart chrome uses a
// fixed slate tone (slate-400) that reads on both light and dark backgrounds.
// The tooltip is the exception: it's real DOM, so it can use theme classes.
const CHROME = "#94a3b8";
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
          className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
            value === opt.id
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:text-foreground"
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
}: {
  icon: typeof Coins;
  label: string;
  value: string;
}) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-4">
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-accent text-primary">
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
    <CartesianGrid strokeDasharray="3 3" stroke={CHROME} strokeOpacity={0.25} />
  );
  const xAxis = (
    <XAxis dataKey="bucket" fontSize={11} tickLine={false} tick={AXIS_TICK} />
  );
  const yAxis = (
    <YAxis
      fontSize={11}
      tickLine={false}
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
    <Card>
      <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <CardTitle className="text-base">{metricLabel} over time</CardTitle>
        <div className="flex flex-wrap gap-2">
          <Segmented options={METRICS} value={metric} onChange={setMetric} />
          <Segmented options={CHART_TYPES} value={chartType} onChange={setChartType} />
        </div>
      </CardHeader>
      <CardContent>
        <ResponsiveContainer width="100%" height={260}>
          {chartType === "area" ? (
            <AreaChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="metricFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={ACCENT} stopOpacity={0.4} />
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
}: {
  stats: PrintStatisticsRead;
  currency: string;
}) {
  if (stats.total_prints === 0) {
    return (
      <Card>
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
    prints: f.print_count,
  }));

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <MetricCard
          icon={Coins}
          label="Total cost"
          value={formatCurrency(stats.total_cost, currency)}
        />
        <MetricCard icon={Boxes} label="Prints" value={String(stats.total_prints)} />
        <MetricCard
          icon={Weight}
          label="Filament used"
          value={formatFilament(stats.total_filament_g)}
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
      </div>

      <TimeSeriesCard stats={stats} currency={currency} />

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Top collections</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={Math.max(160, collectionData.length * 36)}>
              <BarChart
                data={collectionData}
                layout="vertical"
                margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
              >
                <XAxis
                  type="number"
                  fontSize={11}
                  tickLine={false}
                  allowDecimals={false}
                  tick={AXIS_TICK}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  fontSize={11}
                  tickLine={false}
                  width={120}
                  tick={AXIS_TICK}
                />
                <Tooltip
                  content={<ChartTooltip valueLabel="Prints" />}
                  cursor={CURSOR_FILL}
                />
                <Bar dataKey="prints" radius={[0, 4, 4, 0]}>
                  {collectionData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Most used filaments</CardTitle>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={Math.max(160, filamentData.length * 36)}>
              <BarChart
                data={filamentData}
                layout="vertical"
                margin={{ top: 0, right: 16, left: 0, bottom: 0 }}
              >
                <XAxis
                  type="number"
                  fontSize={11}
                  tickLine={false}
                  allowDecimals={false}
                  tick={AXIS_TICK}
                />
                <YAxis
                  type="category"
                  dataKey="name"
                  fontSize={11}
                  tickLine={false}
                  width={120}
                  tick={AXIS_TICK}
                />
                <Tooltip
                  content={<ChartTooltip valueLabel="Prints" />}
                  cursor={CURSOR_FILL}
                />
                <Bar dataKey="prints" radius={[0, 4, 4, 0]}>
                  {filamentData.map((_, i) => (
                    <Cell key={i} fill={BAR_COLORS[i % BAR_COLORS.length]} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function StatisticsPage() {
  const [period, setPeriod] = useState<StatsPeriod>("30d");
  const { data, isLoading, isError } = usePrintStatistics(period);
  const { data: config } = useVaultConfig();
  const currency = config?.currency ?? "USD";

  return (
    <PageContainer>
      <PageHeader
        title="Statistics"
        description="Cost, filament and print activity from completed jobs"
        actions={
          <Segmented options={PERIODS.map((p) => ({ id: p.value, label: p.label }))} value={period} onChange={setPeriod} />
        }
      />

      {isLoading && (
        <div className="py-16 text-center text-sm text-muted-foreground">
          Loading statistics…
        </div>
      )}
      {isError && (
        <Card>
          <CardContent className="py-12 text-center text-sm text-destructive">
            Failed to load statistics.
          </CardContent>
        </Card>
      )}
      {data && <StatsContent stats={data} currency={currency} />}
    </PageContainer>
  );
}
