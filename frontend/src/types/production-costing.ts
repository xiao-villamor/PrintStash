import type { EditingBase } from "./editing";

export interface PrinterExpenses {
  acquisition_cost: number;
  lifetime_hours: number;
  lifetime_maintenance: number;
  power_watts: number;
  electricity_per_kwh: number;
  extra_per_hour: number;
}

export interface PricingRates {
  margin_percent: number;
  tax_percent: number;
}

export interface PricingTemplate extends PricingRates {
  id: string;
  name: string;
}

export interface CostedMaterial {
  name: string;
  grams: number;
  cost_per_kg: number;
  spool_id: number | null;
}

export interface ProductionInputs {
  pieces: number;
  print_minutes: number;
  materials: CostedMaterial[];
  printer: { name: string; expenses: PrinterExpenses } | null;
  pricing: PricingRates;
}

export interface ProductionEstimate extends ProductionInputs {
  currency: string;
}

export interface PieceCosts {
  material: number;
  amortization: number;
  maintenance: number;
  electricity: number;
  extra: number;
  production: number;
  price_before_tax: number;
  tax: number;
  final_price: number;
}

export interface ArtifactCostingRead extends EditingBase {
  currency: string;
  estimate: ProductionEstimate | null;
  costs: PieceCosts | null;
}
