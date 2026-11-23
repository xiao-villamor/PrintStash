import type { PrinterRead, PrinterSnapshot } from "@/types";
import { finiteReading } from "./telemetry";

export const PREHEAT_PRESETS = [
  { name: "PLA", hotend: 200, bed: 60 },
  { name: "PETG", hotend: 240, bed: 80 },
  { name: "ABS", hotend: 245, bed: 100 },
];

export function printerControlAvailability(
  printer: PrinterRead,
  snapshot: PrinterSnapshot,
  live: boolean,
) {
  const connected = live && printer.access.can_control;
  const moonraker = printer.provider === "moonraker" && printer.capabilities.can_send_gcode;
  const controllerReady =
    live && printer.provider === "moonraker" && snapshot.webhooks?.state === "ready";
  const operational = controllerReady && connected && moonraker;
  const state = snapshot.print_stats?.state;
  const idleState = state === "standby" || state === "complete" || state === "cancelled";
  const canIdle = operational && idleState;
  const readyIdle = controllerReady && idleState && snapshot.idle_timeout?.state !== "Printing";
  return {
    connected,
    readyIdle,
    moonraker,
    idle: canIdle,
    homing: operational && readyIdle,
    tuning: canIdle || (operational && (state === "printing" || state === "paused")),
    extrusion:
      canIdle &&
      snapshot.toolhead?.extruder === "extruder" &&
      snapshot.extruder?.can_extrude === true &&
      snapshot.gcode_move?.absolute_coordinates != null &&
      snapshot.gcode_move.absolute_extrude != null &&
      (finiteReading(snapshot.gcode_move.extrude_factor) ?? 0) > 0,
  };
}

export type PrinterCommandPanel =
  | "job"
  | "hotend"
  | "bed"
  | "thermal-presets"
  | "toolhead"
  | "tune-fan"
  | "tune-speed"
  | "tune-flow"
  | "filament"
  | "measure-bed";

export type PrinterCommandReceipt = { kind: "success" | "error"; message: string };
