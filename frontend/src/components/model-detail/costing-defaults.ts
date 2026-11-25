import { machineCostBreakdown } from "@/lib/printer-expenses";
import type { FilamentProfileRead, PrinterProfileRead } from "@/types";
import type { PieceCosts, ProductionInputs } from "@/types/production-costing";

const normalize = (value: string | null | undefined) => value?.trim().toLowerCase() || null;

export function matchFilamentPreset(
  profiles: FilamentProfileRead[],
  material: { material_brand?: string | null; material_type?: string | null } | undefined,
) {
  const brand = normalize(material?.material_brand);
  const type = normalize(material?.material_type);
  const names = brand && type ? [`${brand} ${type}`, brand] : [brand ?? type];
  for (const name of names) {
    if (name === null) continue;
    const exact = profiles.filter(
      (profile) =>
        normalize(profile.name) === name &&
        (type === null ||
          profile.material_type == null ||
          normalize(profile.material_type) === type),
    );
    if (exact.length > 0)
      return exact.length === 1 && exact[0]?.cost_per_kg != null ? exact[0] : undefined;
  }

  const matches = profiles.filter(
    (profile) =>
      type !== null &&
      normalize(profile.material_type) === type &&
      normalize(profile.material_brand) === brand,
  );
  return matches.length === 1 && matches[0]?.cost_per_kg != null ? matches[0] : undefined;
}

export function matchingPrinterPresets<
  T extends Pick<PrinterProfileRead, "name" | "printer_model" | "nozzle_diameter_mm">,
>(
  profiles: readonly T[],
  printerModel: string | null | undefined,
  nozzle: number | null | undefined,
) {
  const model = normalize(printerModel);
  if (!model) return [];
  const compatible = (profile: T) =>
    nozzle == null || profile.nozzle_diameter_mm == null || profile.nozzle_diameter_mm === nozzle;
  const exact = profiles.filter(
    (profile) => normalize(profile.name) === model && compatible(profile),
  );
  const matches = exact.length
    ? exact
    : profiles.filter(
        (profile) => normalize(profile.printer_model) === model && compatible(profile),
      );
  const sameNozzle =
    nozzle == null ? [] : matches.filter((profile) => profile.nozzle_diameter_mm === nozzle);
  return sameNozzle.length ? sameNozzle : matches;
}

export function embeddedRatePerKg(
  materials: { filament_cost?: number | null; filament_weight_g?: number | null }[],
): number | null {
  let cost = 0;
  let grams = 0;
  for (const material of materials) {
    if (
      material.filament_cost == null ||
      material.filament_cost < 0 ||
      material.filament_weight_g == null
    )
      return null;
    cost += material.filament_cost;
    grams += material.filament_weight_g;
  }
  return grams > 0 ? (cost * 1000) / grams : null;
}

/** Preview the current draft. Saved amounts always come from the API. */
export function previewPieceCosts(estimate: ProductionInputs): PieceCosts {
  const hoursPerPiece = estimate.print_minutes / 60 / estimate.pieces;
  const machine = estimate.printer && machineCostBreakdown(estimate.printer.expenses);
  const material =
    estimate.materials.reduce((total, line) => total + (line.grams / 1000) * line.cost_per_kg, 0) /
    estimate.pieces;
  const amortization = (machine?.depreciation ?? 0) * hoursPerPiece;
  const maintenance = (machine?.maintenance ?? 0) * hoursPerPiece;
  const electricity = (machine?.electricity ?? 0) * hoursPerPiece;
  const extra = (machine?.additional ?? 0) * hoursPerPiece;
  const production = material + amortization + maintenance + electricity + extra;
  const price_before_tax = production / (1 - estimate.pricing.margin_percent / 100);
  const tax = (price_before_tax * estimate.pricing.tax_percent) / 100;
  return {
    material,
    amortization,
    maintenance,
    electricity,
    extra,
    production,
    price_before_tax,
    tax,
    final_price: price_before_tax + tax,
  };
}
