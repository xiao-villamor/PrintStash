import type { PrinterCreate, PrinterProvider, PrinterRead } from "@/types";

export type PrinterSetupKind =
  | "moonraker"
  | "elegoo_neptune4"
  | "elegoo_centauri_carbon"
  | "elegoo_centauri_carbon_2"
  | "prusalink"
  | "octoprint"
  | "bambu_lan";

export const PRINTER_SETUP_OPTIONS: Array<{
  value: PrinterSetupKind;
  label: string;
  description: string;
}> = [
  {
    value: "moonraker",
    label: "Moonraker / Klipper",
    description: "Generic Klipper printer using Moonraker.",
  },
  {
    value: "elegoo_neptune4",
    label: "Elegoo Neptune 4 family",
    description: "Neptune 4, Pro, Plus, or Max using its Moonraker service.",
  },
  {
    value: "prusalink",
    label: "PrusaLink (beta)",
    description: "Local Prusa FDM connection; Prusa Connect cloud is not used.",
  },
  {
    value: "octoprint",
    label: "OctoPrint (beta)",
    description: "Local OctoPrint/OctoPi instance using an API key.",
  },
  {
    value: "elegoo_centauri_carbon",
    label: "Elegoo Centauri Carbon (beta)",
    description: "Local SDCP monitoring and controls; file upload is not available.",
  },
  {
    value: "elegoo_centauri_carbon_2",
    label: "Elegoo Centauri Carbon 2 (beta)",
    description: "Local MQTT monitoring and controls; enable LAN Only on printer first.",
  },
  {
    value: "bambu_lan",
    label: "Bambu LAN (beta)",
    description: "Local-network connection using serial and access code.",
  },
];

// Curated so the model picker on the printer card is a select, not free text
// (avoids typos). Not exhaustive — "Other" in the picker covers anything
// missing here with a one-off custom label.
export const PRINTER_MODEL_OPTIONS: string[] = [
  "Bambu Lab A1 mini",
  "Bambu Lab A1",
  "Bambu Lab P1P",
  "Bambu Lab P1S",
  "Bambu Lab X1",
  "Bambu Lab X1 Carbon",
  "Bambu Lab X1E",
  "Bambu Lab H2D",
  "Bambu Lab H2S",
  "Prusa MINI+",
  "Prusa MK3S+",
  "Prusa MK4",
  "Prusa MK4S",
  "Prusa CORE One",
  "Prusa XL",
  "Elegoo Neptune 3",
  "Elegoo Neptune 3 Pro",
  "Elegoo Neptune 3 Plus",
  "Elegoo Neptune 3 Max",
  "Elegoo Neptune 4",
  "Elegoo Neptune 4 Pro",
  "Elegoo Neptune 4 Plus",
  "Elegoo Neptune 4 Max",
  "Elegoo Centauri Carbon",
  "Elegoo Centauri Carbon 2",
  "Creality Ender 3",
  "Creality Ender 3 Pro",
  "Creality Ender 3 V2",
  "Creality Ender 3 V2 Neo",
  "Creality Ender 3 V3",
  "Creality Ender 3 V3 SE",
  "Creality Ender 3 V3 KE",
  "Creality Ender 3 V3 Plus",
  "Creality Ender 3 V4",
  "Creality Ender 3 S1",
  "Creality Ender 3 S1 Pro",
  "Creality Ender 3 S1 Plus",
  "Creality Ender 5",
  "Creality Ender 5 Pro",
  "Creality Ender 5 Plus",
  "Creality Ender 5 S1",
  "Creality Ender 5 Max",
  "Creality Ender 6",
  "Creality Hi",
  "Creality K1",
  "Creality K1 Max",
  "Creality K1C",
  "Creality K1 SE",
  "Creality K2",
  "Creality K2 Plus",
  "Creality K2 Pro",
  "Creality K2 SE",
  "Creality CR-10",
  "Creality CR-10 V2",
  "Creality CR-10 V3",
  "Creality CR-10 SE",
  "Creality CR-10 Max",
  "Creality CR-6 SE",
  "Creality CR-6 Max",
  "Creality CR-M4",
  "Creality Sermoon V1",
  "Voron 0",
  "Voron 2.4",
  "Voron Trident",
  "Voron Switchwire",
  "RatRig V-Core 3",
  "Sovol SV06",
  "Sovol SV06 Plus",
  "Sovol SV07",
  "Sovol SV08",
  "Anycubic Vyper",
  "Anycubic Kobra 2",
  "Anycubic Kobra 3",
  "Anycubic Kobra 3 Max",
  "Anycubic Kobra S1",
  "Flashforge Adventurer 5M",
  "Flashforge Adventurer 5M Pro",
  "Qidi Q1 Pro",
  "Qidi X-Plus 3",
  "Qidi X-Max 3",
];

export function providerLabel(
  value: Pick<PrinterRead, "provider" | "provider_variant"> | PrinterProvider,
): string {
  const printer = typeof value === "string" ? { provider: value, provider_variant: null } : value;
  if (printer.provider === "prusalink") return "PrusaLink";
  if (printer.provider === "octoprint") return "OctoPrint";
  if (printer.provider === "bambu_lan") return "Bambu LAN";
  if (printer.provider_variant === "elegoo_centauri_carbon_2") return "Elegoo Centauri Carbon 2";
  if (printer.provider_variant === "elegoo_centauri_carbon") return "Elegoo Centauri Carbon";
  if (printer.provider_variant === "elegoo_neptune4") return "Elegoo Neptune 4 / Moonraker";
  return "Moonraker";
}

export function providerAddress(
  printer: Pick<
    PrinterRead,
    | "provider"
    | "moonraker_url"
    | "bambu_host"
    | "prusalink_url"
    | "elegoo_centauri_host"
    | "octoprint_url"
  >,
): string {
  if (printer.provider === "prusalink") return printer.prusalink_url || "PrusaLink";
  if (printer.provider === "octoprint") return printer.octoprint_url || "OctoPrint";
  if (printer.provider === "bambu_lan") return printer.bambu_host || "Bambu LAN";
  if (printer.provider === "elegoo_centauri") {
    return printer.elegoo_centauri_host || "Elegoo Centauri";
  }
  return printer.moonraker_url;
}

export function setupProviderFields(kind: PrinterSetupKind): Pick<
  PrinterCreate,
  "provider" | "provider_variant"
> {
  if (kind === "elegoo_neptune4") {
    return { provider: "moonraker", provider_variant: "elegoo_neptune4" };
  }
  if (kind === "elegoo_centauri_carbon" || kind === "elegoo_centauri_carbon_2") {
    return { provider: "elegoo_centauri", provider_variant: kind };
  }
  return { provider: kind };
}
