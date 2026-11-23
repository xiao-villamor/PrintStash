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
      readings: snapshot.extruder,
      samples: history.extruder,
      stroke: "text-chart-cyan",
    },
    {
      id: "heater_bed" as const,
      label: uiText("Bed"),
      readings: snapshot.heater_bed,
      samples: history.heater_bed,
      stroke: "text-chart-violet",
    },
  ].map((entry) => {
    const segments = heaterSegments(
      entry.samples.filter((sample) => sample.at >= end - HEATER_WINDOW_MS),
    );
    return { ...entry, segments, observed: segments.flat(), shown: visibility[entry.id] };
  });
  const visible = series.flatMap((entry) => entry.observed);
  const span = Math.min(
    HEATER_WINDOW_MS,
    Math.max(15_000, ...visible.map((sample) => end - sample.at)),
  );
  const max = Math.ceil(Math.max(40, ...visible.map((sample) => sample.degrees)) / 40) * 40;
  const min = Math.floor(Math.min(0, ...visible.map((sample) => sample.degrees)) / 40) * 40;
  const width = 640;
  const height = 64;
  const x = (at: number) => (1 - (end - at) / span) * (width - 4) + 2;
  const y = (value: number) => 2 + (1 - (value - min) / (max - min)) * (height - 4);
  const ticks = [max, (max + min) / 2, min];

  return (
    <figure aria-label={uiText("Heater history")} className="space-y-2">
      {series.map((entry) => {
        const id = `${plotId}-${entry.id}`;
        const current = finiteReading(entry.readings?.temperature);
        const target = finiteReading(entry.readings?.target);
        const last = entry.observed.at(-1);
        return (
          <div key={entry.id} className="printer-heater-lane border-b border-border pb-2">
            <dl className="min-w-0 space-y-0.5">
              <dt>
                <button
                  type="button"
                  aria-label={uiText("Plot {value1}", { value1: entry.label })}
                  aria-pressed={entry.shown}
                  aria-controls={id}
                  className="flex items-center gap-2 text-xs font-medium text-muted-foreground transition-colors duration-press hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:transition-none"
                  onClick={() =>
                    setVisibility((previous) => ({ ...previous, [entry.id]: !previous[entry.id] }))
                  }
                >
                  <span
                    aria-hidden="true"
                    className={`w-3 border-t-2 border-current ${entry.stroke}`}
                  />
                  {entry.label}
                </button>
              </dt>
              <dd className="font-mono text-lg font-semibold leading-tight tabular-nums">
                {current === null ? uiText("Unavailable") : degrees(current)}
              </dd>
              {target !== null && (
                <dd className="text-xs text-muted-foreground">
                  {uiText("Target {value1}", { value1: degrees(target) })}
                </dd>
              )}
              {renderTarget && <dd className="pt-1">{renderTarget(entry.id)}</dd>}
            </dl>
            <div id={id} role="group" aria-label={entry.label} className="printer-heater-plot h-16">
              <div
                aria-hidden="true"
                className="flex flex-col justify-between text-end font-mono text-2xs text-muted-foreground"
              >
                {ticks.map((tick) => (
                  <span key={tick}>{degrees(tick)}</span>
                ))}
              </div>
              <div className="relative my-1.5 min-h-0 min-w-0">
                <div
                  aria-hidden="true"
                  className="absolute inset-0.5 flex flex-col justify-between"
                >
                  {ticks.map((tick) => (
                    <div key={tick} className="border-t border-border" />
                  ))}
                </div>
                {entry.shown && (
                  <svg
                    viewBox={`0 0 ${width} ${height}`}
                    preserveAspectRatio="none"
                    aria-hidden="true"
                    className={`absolute inset-0 size-full ${entry.stroke}`}
                  >
                    {entry.segments
                      .filter((segment) => segment.length > 1)
                      .map((segment) => (
                        <polyline
                          key={segment[0].at}
                          fill="none"
                          stroke="currentColor"
                          strokeWidth={2}
                          vectorEffect="non-scaling-stroke"
                          points={segment
                            .map((sample) => `${x(sample.at)},${y(sample.degrees)}`)
                            .join(" ")}
                        />
                      ))}
                  </svg>
                )}
                {(!entry.shown || !last) && (
                  <p className="absolute inset-0 grid place-items-center text-center text-xs text-muted-foreground">
                    {entry.shown ? uiText("No heater readings yet") : uiText("Plot hidden")}
                  </p>
                )}
              </div>
              {entry.shown && last && (
                <span className="sr-only">
                  {uiText("{value1} observed range: {value2} to {value3}", {
                    value1: entry.label,
                    value2: degrees(Math.min(...entry.observed.map((sample) => sample.degrees))),
                    value3: degrees(Math.max(...entry.observed.map((sample) => sample.degrees))),
                  })}
                </span>
              )}
            </div>
            {entry.shown && current === null && last && (
              <p className="text-xs text-muted-foreground sm:col-start-2">
                {uiText("Last observed {value1}", { value1: degrees(last.degrees) })}
              </p>
            )}
          </div>
        );
      })}
      <div aria-hidden="true" className="printer-heater-lane">
        <div className="printer-heater-plot sm:col-start-2">
          <div className="col-start-2 flex justify-between px-0.5 font-mono text-2xs text-muted-foreground">
            <span>
              {uiText("{value1} ago", { value1: formatDuration(Math.ceil(span / 1000)) })}
            </span>
            <span>{uiText("Now")}</span>
          </div>
        </div>
      </div>
      <figcaption className="text-xs text-muted-foreground">
        {!live
          ? uiText("Telemetry disconnected")
          : recorded
            ? uiText("Moonraker history and live readings · historical times approximate")
            : uiText("Observed temperatures in this session")}
      </figcaption>
    </figure>
  );
}

export function PartCoolingFan({
  speed,
  controls,
  variant = "panel",
}: {
  speed: number | null | undefined;
  controls?: ReactNode;
  variant?: "panel" | "reading";
}) {
  const compact = controls !== undefined;
  const smallRotor = compact || variant === "reading";
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
      className={`grid shrink-0 place-items-center border border-border bg-muted ${smallRotor ? "size-7" : "size-14"}`}
    >
      <div
        className={`grid place-items-center rounded-full border border-border bg-card ${smallRotor ? "size-6" : "size-10"}`}
      >
        <Fan
          className={`transition-transform duration-fast ease-out motion-reduce:transition-none ${smallRotor ? "size-4" : "size-7"} ${percent !== null && percent > 0 ? "text-chart-cyan" : "text-muted-foreground"}`}
          style={{ transform: `rotate(${percent === null ? 0 : percent * 2.7}deg)` }}
        />
      </div>
    </div>
  );
  if (variant === "reading") {
    if (percent === null) return null;
    return (
      <section aria-label={uiText("Part cooling fan")} className="flex items-start gap-2">
        {rotor}
        <div>
          <h3 className="text-xs text-muted-foreground">{uiText("Part cooling fan")}</h3>
          <p className="font-mono text-xl font-semibold tabular-nums">
            {formatNumber(percent, { maximumFractionDigits: 1 })}%
          </p>
        </div>
      </section>
    );
  }
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
