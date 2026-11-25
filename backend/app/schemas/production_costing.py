"""Inputs and receipts for per-piece production costing."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.schemas.editing import EditingBase

Amount = Annotated[float, Field(ge=0, le=1_000_000)]
Label = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]


class PrinterExpenses(BaseModel):
    model_config = ConfigDict(extra="forbid")

    acquisition_cost: Amount
    lifetime_hours: Annotated[float, Field(ge=1, le=1_000_000)]
    lifetime_maintenance: Amount
    power_watts: Amount
    electricity_per_kwh: Amount
    extra_per_hour: Amount


class PricingRates(BaseModel):
    model_config = ConfigDict(extra="forbid")

    margin_percent: Annotated[float, Field(ge=0, le=99.99)]
    tax_percent: Annotated[float, Field(ge=0, le=100)]


class PricingTemplateCreate(PricingRates):
    name: Label


class PricingTemplateRead(PricingTemplateCreate):
    id: str


class CostedMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Label
    grams: Amount
    cost_per_kg: Amount
    spool_id: Annotated[int, Field(gt=0)] | None = None


class CostedPrinter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Label
    expenses: PrinterExpenses


class ProductionInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pieces: Annotated[int, Field(strict=True, ge=1, le=100_000)]
    print_minutes: Amount
    materials: Annotated[list[CostedMaterial], Field(min_length=1, max_length=32)]
    printer: CostedPrinter | None
    pricing: PricingRates


class ArtifactCostingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimate: ProductionInputs | None


class ProductionEstimate(ProductionInputs):
    currency: Annotated[str, Field(min_length=3, max_length=3)]


class PieceCosts(BaseModel):
    material: float
    amortization: float
    maintenance: float
    electricity: float
    extra: float
    production: float
    price_before_tax: float
    tax: float
    final_price: float


class ArtifactCostingRead(EditingBase):
    currency: str
    estimate: ProductionEstimate | None
    costs: PieceCosts | None
    slicer_filament_cost: float | None
    multiple_filaments: bool


class JobCostRates(BaseModel):
    currency: str
    machine_per_hour: float
    material_per_kg: float | None
    slicer_seconds: int | None
    slicer_grams: float | None


class JobProductionCost(BaseModel):
    currency: str
    material: float | None
    machine: float | None
    total: float
    estimated: bool
