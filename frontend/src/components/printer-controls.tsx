import { useId, useState } from "react";
import { Button, buttonVariants } from "@/components/ui/button";
import { PrinterFeedRows } from "@/components/printer-machine";
import type { useMachineSetup } from "@/features/printers/materials";
import { Link } from "@/lib/link";
import { PartCoolingFan } from "@/components/printer-telemetry";
import { Input, inputClasses } from "@/components/ui/input";
import { extrudePrinter, homePrinter, jogPrinter, tunePrinter } from "@/lib/api/printers";
import {
  PREHEAT_PRESETS,
  printerControlAvailability,
  type PrinterCommandPanel,
  type PrinterCommandReceipt,
} from "@/features/printers/controls";
import { finiteReading } from "@/features/printers/telemetry";
import { formatNumber } from "@/lib/format";
import { cn } from "@/lib/utils";
import { uiMessage, uiText, type MessageDescriptor } from "@/lib/locale";
import type { PrinterRead, PrinterSnapshot } from "@/types";
import type { PrinterAxis, PrinterTuning } from "@/types/printers";

const AXES = ["x", "y", "z"] as const;
const STEPS = [0.1, 1, 10] as const;
const MOVE = [
  { axis: "y", direction: 1, column: 2, row: 1 },
  { axis: "x", direction: -1, column: 1, row: 2 },
  { axis: "x", direction: 1, column: 3, row: 2 },
  { axis: "y", direction: -1, column: 2, row: 3 },
  { axis: "z", direction: 1, column: 4, row: 1 },
  { axis: "z", direction: -1, column: 4, row: 3 },
] as const;

export type CommandReceipts = Partial<
  Record<PrinterCommandPanel | "emergency", PrinterCommandReceipt>
>;

export function CommandReceipt({
  receipt,
  pending,
  className = "",
}: {
  receipt: PrinterCommandReceipt | undefined;
  pending: boolean;
  className?: string;
}) {
  return (
    <>
      <div role="status" className={`text-xs text-muted-foreground empty:sr-only ${className}`}>
        {pending
          ? uiText("Sending command…")
          : receipt?.kind === "success"
            ? receipt.message
            : null}
      </div>
      {receipt?.kind === "error" && !pending && (
        <p role="alert" className={`text-xs text-destructive ${className}`}>
          {receipt.message}
        </p>
      )}
    </>
  );
}

export function PrinterControls({
  printer,
  snapshot,
  live,
  busy,
  pending,
  receipts,
  setup,
  materialsHref,
  run,
}: {
  printer: PrinterRead;
  snapshot: PrinterSnapshot;
  live: boolean;
  busy: boolean;
  pending: PrinterCommandPanel | null;
  receipts: CommandReceipts;
  setup: ReturnType<typeof useMachineSetup>;
  materialsHref: string | undefined;
  run: (
    key: PrinterCommandPanel,
    command: () => Promise<void>,
    success: MessageDescriptor,
  ) => Promise<void>;
}) {
  const [step, setStep] = useState<(typeof STEPS)[number]>(1);
  const availability = printerControlAvailability(printer, snapshot, live);
  const homed = snapshot.toolhead?.homed_axes?.toLowerCase();
  const ready = availability.homing && !busy;
  const allHomed = AXES.every((axis) => homed?.includes(axis));
  const home = (axis?: PrinterAxis) =>
    void run(
      "toolhead",
      () => homePrinter(printer.id, axis),
      axis
        ? uiMessage("Home {value1} command sent", { value1: axis.toUpperCase() })
        : uiMessage("Home all command sent"),
    );

  return (
    <div className="printer-workbench-column">
      <section aria-label={uiText("Toolhead")} className="printer-desk-section">
        <div className="printer-desk-heading">
          <h2>{uiText("Toolhead")}</h2>
          <span>
            {homed
              ? uiText("Homed {value1}", { value1: homed.toUpperCase() })
              : homed === ""
                ? uiText("Not homed")
                : uiText("Homing unknown")}
          </span>
        </div>
        <dl className="flex flex-wrap gap-x-7 gap-y-2">
          {AXES.map((axis, index) => (
            <div key={axis} className="flex items-baseline gap-2">
              <dt className="text-xs text-muted-foreground">{axis.toUpperCase()}</dt>
              <dd className="font-mono text-sm font-medium">
                {measurement(snapshot.toolhead?.position?.[index])}
              </dd>
            </div>
          ))}
        </dl>
        <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
          <div
            className="grid grid-cols-4 gap-1.5"
            aria-label={uiText("Jog toolhead")}
            role="group"
          >
            {MOVE.map(({ axis, direction, column, row }) => (
              <Button
                key={`${axis}${direction}`}
                size="sm"
                variant="outline"
                className="w-11 font-mono"
                style={{ gridColumn: column, gridRow: row }}
                disabled={
                  !ready ||
                  !homed?.includes(axis) ||
                  snapshot.gcode_move?.absolute_coordinates == null
                }
                onClick={() =>
                  void run(
                    "toolhead",
                    () => jogPrinter(printer.id, axis, direction * step),
                    uiMessage("Move sent"),
                  )
                }
              >
                {`${axis.toUpperCase()}${direction > 0 ? "+" : "−"}`}
              </Button>
            ))}
            <Button
              size="sm"
              variant="outline"
              className="col-start-2 row-start-2 w-11"
              aria-label={uiText("Home all")}
              disabled={!ready}
              onClick={() => home()}
            >
              ⌂
            </Button>
          </div>
          <div className="space-y-2">
            <h3 className="text-xs font-medium">{uiText("Move distance")}</h3>
            <div role="group" aria-label={uiText("Move distance")} className="flex gap-1.5">
              {STEPS.map((distance) => (
                <Button
                  key={distance}
                  size="xs"
                  variant="outline"
                  aria-pressed={step === distance}
                  className="font-mono aria-pressed:bg-accent aria-pressed:text-accent-foreground"
                  onClick={() => setStep(distance)}
                >
                  {millimetres(distance)}
                </Button>
              ))}
            </div>
            <div className="flex flex-wrap gap-1.5">
              {AXES.map((axis) => (
                <Button
                  key={axis}
                  size="xs"
                  variant="outline"
                  disabled={!ready}
                  onClick={() => home(axis)}
                >
                  {uiText("Home {value1}", { value1: axis.toUpperCase() })}
                </Button>
              ))}
            </div>
          </div>
        </div>
        {(!availability.homing || !allHomed) && (
          <p className="text-xs text-muted-foreground">
            {!printer.access.can_control
              ? uiText("Control role required.")
              : !live
                ? uiText("Waiting for live printer status.")
                : !availability.homing
                  ? uiText("Motion is available when the printer is idle and ready.")
                  : uiText("Home an axis before moving it.")}
          </p>
        )}
        <CommandReceipt receipt={receipts.toolhead} pending={pending === "toolhead"} />
      </section>
      <section className="printer-desk-section" aria-label={uiText("Print tuning")}>
        <div className="printer-desk-heading">
          <h2>{uiText("Print tuning")}</h2>
        </div>
        <div className="divide-y divide-border">
          {(
            [
              {
                control: "fan",
                label: uiText("Part cooling fan"),
                factor: snapshot.fan?.speed,
                min: 0,
                max: 100,
              },
              {
                control: "speed",
                label: uiText("Print speed"),
                factor: snapshot.gcode_move?.speed_factor,
                min: 50,
                max: 150,
              },
              {
                control: "flow",
                label: uiText("Extrusion flow"),
                factor: snapshot.gcode_move?.extrude_factor,
                min: 50,
                max: 150,
              },
            ] as const
          ).map(({ control, label, factor, min, max }) => (
            <TuningRow
              key={control}
              control={control}
              label={label}
              measured={finiteReading(factor)}
              min={min}
              max={max}
              disabled={!availability.tuning || !canTuneFactor(control, factor)}
              busy={busy}
              pending={pending === `tune-${control}`}
              receipt={receipts[`tune-${control}`]}
              onApply={(percent) =>
                void run(
                  `tune-${control}`,
                  () => tunePrinter(printer.id, control, percent),
                  uiMessage("Tuning command sent"),
                )
              }
            />
          ))}
        </div>
      </section>
      <section className="printer-desk-section" aria-label={uiText("Filament")}>
        <div className="printer-desk-heading">
          <h2>{uiText("Filament")}</h2>
          {materialsHref && (
            <Link href={materialsHref} className={buttonVariants({ variant: "ghost", size: "xs" })}>
              {uiText("Materials & maintenance")}
            </Link>
          )}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <PrinterFeedRows setup={setup} />
          <div className="flex flex-wrap items-center gap-2">
            {(
              [
                ["Feed", 5],
                ["Retract", -5],
              ] as const
            ).map(([label, distance]) => (
              <Button
                key={label}
                variant="outline"
                size="xs"
                disabled={!availability.extrusion || busy}
                onClick={() =>
                  void run(
                    "filament",
                    () => extrudePrinter(printer.id, distance),
                    uiMessage("Filament command sent"),
                  )
                }
              >
                {uiText(label)} {millimetres(Math.abs(distance))}
              </Button>
            ))}
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          {!printer.access.can_control
            ? uiText("Control role required.")
            : !live
              ? uiText("Waiting for live printer status.")
              : availability.extrusion
                ? uiText("Hotend ready for filament movement.")
                : uiText("Heat the hotend while idle to enable filament movement.")}
        </p>
        <CommandReceipt receipt={receipts.filament} pending={pending === "filament"} />
      </section>
    </div>
  );
}

function canTuneFactor(control: "fan" | "speed" | "flow", factor: number | null | undefined) {
  const value = finiteReading(factor);
  return value !== null && (control === "fan" ? value >= 0 && value <= 1 : value > 0);
}

function millimetres(value: number) {
  return uiText("{value1} mm", { value1: formatNumber(value, { maximumFractionDigits: 2 }) });
}

function measurement(value: number | null | undefined) {
  const number = finiteReading(value);
  return number === null ? "—" : millimetres(number);
}

function TuningRow({
  control,
  label,
  measured,
  min,
  max,
  disabled,
  busy,
  pending,
  receipt,
  onApply,
}: {
  control: PrinterTuning;
  label: string;
  measured: number | null;
  min: number;
  max: number;
  disabled: boolean;
  busy: boolean;
  pending: boolean;
  receipt: PrinterCommandReceipt | undefined;
  onApply: (percent: number) => void;
}) {
  const id = useId();
  const [value, setValue] = useState("");
  const percent = Number(value);
  const valid = value !== "" && Number.isFinite(percent) && percent >= min && percent <= max;
  const controls = (
    <>
      <Input
        id={id}
        aria-label={uiText("{value1} target (%)", { value1: label })}
        type="number"
        min={min}
        max={max}
        step="any"
        value={value}
        placeholder="%"
        className="h-7 w-full font-mono"
        disabled={disabled}
        readOnly={busy}
        onChange={(event) => setValue(event.target.value)}
      />
      <Button
        size="xs"
        className="h-7"
        variant="outline"
        type="submit"
        disabled={disabled || busy || !valid}
        aria-label={uiText("Set {value1}", { value1: label })}
      >
        {uiText("Set")}
      </Button>
    </>
  );

  return (
    <form
      className="space-y-1 py-0.5 first:pt-0 last:pb-0"
      onSubmit={(event) => {
        event.preventDefault();
        if (!disabled && !busy && valid) onApply(percent);
      }}
    >
      {control === "fan" ? (
        <PartCoolingFan speed={measured} controls={controls} />
      ) : (
        <div className="printer-tuning-row">
          <label className="min-w-0 text-xs" htmlFor={id}>
            {label}
          </label>
          <div className="printer-tuning-fields">
            <span className="font-mono text-sm font-medium tabular-nums">
              {measured === null
                ? "—"
                : `${formatNumber(measured * 100, { maximumFractionDigits: 1 })}%`}
            </span>
            {controls}
          </div>
        </div>
      )}
      <CommandReceipt receipt={receipt} pending={pending} />
    </form>
  );
}

export function HeaterTarget({
  label,
  disabled,
  busy,
  pending,
  receipt,
  onApply,
}: {
  label: string;
  disabled: boolean;
  busy: boolean;
  pending: boolean;
  receipt: PrinterCommandReceipt | undefined;
  onApply: (degrees: number, clear: () => void) => Promise<void>;
}) {
  const [draft, setDraft] = useState("");
  const degrees = Number(draft);
  const valid = draft !== "" && Number.isFinite(degrees) && degrees >= 0 && degrees <= 500;

  return (
    <form
      className="space-y-1"
      onSubmit={(event) => {
        event.preventDefault();
        if (!disabled && !busy && valid) void onApply(degrees, () => setDraft(""));
      }}
    >
      <div className="flex items-center gap-2">
        <Input
          type="number"
          aria-label={label}
          min={0}
          max={500}
          step="any"
          placeholder="°C"
          value={draft}
          disabled={disabled}
          readOnly={busy}
          onChange={(event) => setDraft(event.target.value)}
          className="h-7 w-20 font-mono"
        />
        <Button
          variant="outline"
          size="xs"
          className="h-7"
          type="submit"
          disabled={disabled || busy || !valid}
          aria-label={uiText("Set {value1}", { value1: label })}
        >
          {uiText("Set")}
        </Button>
      </div>
      <CommandReceipt receipt={receipt} pending={pending} />
    </form>
  );
}

export function ThermalPresets({
  disabled,
  activePrint,
  preheat,
  cooldown,
  pending,
  receipt,
}: {
  disabled: boolean;
  activePrint: boolean;
  preheat: (preset: (typeof PREHEAT_PRESETS)[number]) => void;
  cooldown: () => void;
  pending: boolean;
  receipt: PrinterCommandReceipt | undefined;
}) {
  const [preset, setPreset] = useState(PREHEAT_PRESETS[0].name);
  const selected = PREHEAT_PRESETS.find((candidate) => candidate.name === preset);

  return (
    <div className="space-y-1.5 border-t border-border pt-2">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex flex-wrap items-center gap-2 text-xs">
          {uiText("Material preset")}
          <select
            className={cn(inputClasses, "h-8 w-auto max-w-full")}
            value={preset}
            disabled={disabled}
            onChange={(event) => setPreset(event.target.value)}
          >
            {PREHEAT_PRESETS.map(({ name, hotend, bed }) => (
              <option key={name} value={name}>
                {name} · {hotend} / {bed} °C
              </option>
            ))}
          </select>
        </label>
        <Button
          size="xs"
          disabled={disabled || !selected}
          onClick={() => selected && preheat(selected)}
        >
          {uiText("Preheat")}
        </Button>
        <Button variant="outline" size="xs" disabled={disabled} onClick={cooldown}>
          {uiText("Cooldown")}
        </Button>
      </div>
      {activePrint && (
        <p className="text-xs text-muted-foreground">
          {uiText("Presets and cooldown are available when the printer is idle.")}
        </p>
      )}
      <CommandReceipt receipt={receipt} pending={pending} />
    </div>
  );
}
