import { useEffect, useId, useState, type ReactNode } from "react";
import { Fan } from "lucide-react";

import {
  finiteReading,
  heaterSegments,
  HEATER_WINDOW_MS,
  type HeaterHistory,
} from "@/features/printers/telemetry";
import { formatDuration, formatNumber } from "@/lib/format";
import { uiText } from "@/lib/locale";
import type { PrinterSnapshot } from "@/types";

export function PrinterProgress({ value }: { value: number }) {
  return (
    <div aria-hidden="true" className="h-1.5 overflow-hidden bg-muted">
      <div
        className="h-full origin-left bg-chart-blue transition-transform duration-fast ease-out motion-reduce:transition-none"
        style={{ transform: `scaleX(${Math.max(0, Math.min(100, value)) / 100})` }}
      />
    </div>
  );
}

const degrees = (value: number) => `${formatNumber(value, { maximumFractionDigits: 1 })} °C`;

export function HeaterChart({
  snapshot,
  history,
  live,
  recorded = false,
  renderTarget,
}: {
  snapshot: PrinterSnapshot;
  history: HeaterHistory;
  live: boolean;
  recorded?: boolean;
  renderTarget?: (heater: keyof HeaterHistory) => ReactNode;
}) {
  const [clock, setClock] = useState(Date.now);
  const plotId = useId();
  const [visibility, setVisibility] = useState({ extruder: true, heater_bed: true });
  useEffect(() => {
    const timer = window.setInterval(() => setClock(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const end = Math.max(clock, history.extruder.at(-1)?.at ?? 0, history.heater_bed.at(-1)?.at ?? 0);
  const series = [
    {
      id: "extruder" as const,
      label: uiText("Hotend"),
      dashed: false,
      readings: snapshot.extruder,
      samples: history.extruder,
      stroke: "text-chart-cyan",
    },
    {
      id: "heater_bed" as const,
      label: uiText("Bed"),
      dashed: true,
      readings: snapshot.heater_bed,
      samples: history.heater_bed,
      stroke: "text-chart-violet",
    },
  ].map((entry) => ({
    ...entry,
    shown: visibility[entry.id],
    samples: entry.samples.filter((sample) => sample.at >= end - HEATER_WINDOW_MS),
  }));
  const plotted = series.filter((entry) => entry.shown);
  const visible = series.flatMap((entry) => entry.samples);
  const span = Math.min(
    HEATER_WINDOW_MS,
    Math.max(15_000, ...visible.map((sample) => end - sample.at)),
  );
  const max = Math.ceil(Math.max(40, ...visible.map((sample) => sample.degrees)) / 40) * 40;
  const min = Math.floor(Math.min(0, ...visible.map((sample) => sample.degrees)) / 40) * 40;
  const width = 640;
  const height = 160;
  const x = (at: number) => (1 - (end - at) / span) * (width - 6) + 3;
  const y = (value: number) => height - ((value - min) / (max - min)) * height;

  return (
    <div className="space-y-3">
      <dl className="printer-heater-readings" aria-label={uiText("Temperatures")}>
        {series.map((entry) => {
          const current = finiteReading(entry.readings?.temperature);
          const target = finiteReading(entry.readings?.target);
          return (
            <div key={entry.id} className="printer-heater-reading">
              <dt>
                <button
                  type="button"
                  aria-label={uiText("Plot {value1}", { value1: entry.label })}
                  aria-pressed={entry.shown}
                  aria-controls={plotId}
                  className="flex items-center gap-2 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring aria-pressed:font-medium"
                  onClick={() =>
                    setVisibility((previous) => ({ ...previous, [entry.id]: !previous[entry.id] }))
                  }
                >
                  <span
                    aria-hidden="true"
                    className={`w-4 border-t-2 border-current ${entry.stroke} ${entry.dashed ? "border-dashed" : ""}`}
                  />
                  {entry.label}
                </button>
              </dt>
              <dd className="font-mono text-xl font-semibold tabular-nums">
                {current === null ? uiText("Unavailable") : degrees(current)}
              </dd>
              {target !== null && (
                <dd className="text-xs text-muted-foreground">
                  {uiText("Target {value1}", { value1: degrees(target) })}
                </dd>
              )}
              {renderTarget && <dd className="printer-heater-edit">{renderTarget(entry.id)}</dd>}
            </div>
          );
        })}
      </dl>
      <figure id={plotId} aria-label={uiText("Heater history")} className="space-y-1.5">
        {plotted.some((entry) => entry.samples.length > 0) ? (
          <>
            <div className="relative flex h-40 gap-2">
              <div
                aria-hidden="true"
                className="flex shrink-0 flex-col justify-between text-right font-mono text-2xs text-muted-foreground"
              >
                {[max, (max + min) / 2, min].map((tick) => (
                  <span key={tick}>{degrees(tick)}</span>
                ))}
              </div>
              <div className="relative min-w-0 flex-1">
                <div aria-hidden="true" className="absolute inset-0 flex flex-col justify-between">
                  {[0, 1, 2].map((line) => (
                    <div key={line} className="border-t border-border" />
                  ))}
                </div>
                <svg
                  viewBox={`0 -3 ${width} ${height + 6}`}
                  preserveAspectRatio="none"
                  aria-hidden="true"
                  className="relative block size-full"
                >
                  {plotted.map((entry) => (
                    <g key={entry.label} className={entry.stroke}>
                      {heaterSegments(entry.samples).map((segment) =>
                        segment.length === 1 ? (
                          <circle
                            key={segment[0].at}
                            cx={x(segment[0].at)}
                            cy={y(segment[0].degrees)}
                            r={entry.dashed ? 2 : 3.5}
                            fill="currentColor"
                          />
                        ) : (
                          <polyline
                            key={segment[0].at}
                            fill="none"
                            stroke="currentColor"
                            strokeWidth={entry.dashed ? 2 : 3}
                            strokeDasharray={entry.dashed ? "6 4" : undefined}
                            vectorEffect="non-scaling-stroke"
                            points={segment
                              .map((sample) => `${x(sample.at)},${y(sample.degrees)}`)
                              .join(" ")}
                          />
                        ),
                      )}
                    </g>
                  ))}
                </svg>
              </div>
            </div>
            <div
              aria-hidden="true"
              className="flex justify-between pl-10 font-mono text-2xs text-muted-foreground"
            >
              <span>
                {uiText("{value1} ago", { value1: formatDuration(Math.ceil(span / 1000)) })}
              </span>
              <span>{uiText("Now")}</span>
            </div>
          </>
        ) : (
          <p className="grid h-40 place-items-center text-center text-sm text-muted-foreground">
            {plotted.length === 0
              ? uiText("No heater series selected")
              : uiText("No heater readings yet")}
          </p>
        )}
        <figcaption className="text-xs text-muted-foreground">
          {!live
            ? uiText("Telemetry disconnected")
            : recorded
              ? uiText("Moonraker history and live readings · historical times approximate")
              : uiText("Observed temperatures in this session")}
          {plotted
            .filter((entry) => entry.samples.length > 0)
            .map((entry) => (
              <span className="sr-only" key={entry.label}>
                {" "}
                {uiText("{value1} observed range: {value2} to {value3}", {
                  value1: entry.label,
                  value2: degrees(Math.min(...entry.samples.map((sample) => sample.degrees))),
                  value3: degrees(Math.max(...entry.samples.map((sample) => sample.degrees))),
                })}
              </span>
            ))}
        </figcaption>
      </figure>
    </div>
  );
}

export function PartCoolingFan({
  speed,
  controls,
}: {
  speed: number | null | undefined;
  controls?: ReactNode;
}) {
  const compact = controls !== undefined;
  const value = finiteReading(speed);
  const percent = value !== null && value >= 0 && value <= 1 ? value * 100 : null;
  const reading = (
    <span className="font-mono text-sm font-medium tabular-nums">
      {percent === null
        ? uiText("Unavailable")
        : `${formatNumber(percent, { maximumFractionDigits: compact ? 1 : 0 })}%`}
    </span>
  );
  const rotor = (
    <div
      aria-hidden="true"
      className={`grid shrink-0 place-items-center border border-border bg-muted ${compact ? "size-7" : "size-14"}`}
    >
      <div
        className={`grid place-items-center rounded-full border border-border bg-card ${compact ? "size-6" : "size-10"}`}
      >
        <Fan
          className={`transition-transform duration-fast ease-out motion-reduce:transition-none ${compact ? "size-4" : "size-7"} ${percent !== null && percent > 0 ? "text-chart-cyan" : "text-muted-foreground"}`}
          style={{ transform: `rotate(${percent === null ? 0 : percent * 2.7}deg)` }}
        />
      </div>
    </div>
  );
  return (
    <section
      aria-label={uiText("Part cooling fan")}
      className={compact ? "printer-tuning-row" : "flex items-center gap-4"}
    >
      {compact ? (
        <>
          <div className="flex min-w-0 items-center gap-2 text-xs">
            {rotor}
            <h3>{uiText("Part cooling fan")}</h3>
          </div>
          <div className="printer-tuning-fields">
            {reading}
            {controls}
          </div>
        </>
      ) : (
        <>
          {rotor}
          <div className="min-w-0 flex-1 space-y-2">
            <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
              <h3 className="text-sm font-medium">{uiText("Part cooling fan")}</h3>
              {reading}
            </div>
            {percent !== null && <PrinterProgress value={percent} />}
          </div>
        </>
      )}
    </section>
  );
}
