"""Calculate reproducible production costs from saved input snapshots."""

from decimal import ROUND_HALF_UP, Decimal

from app.schemas.production_costing import PieceCosts, ProductionEstimate


def piece_costs(estimate: ProductionEstimate) -> PieceCosts:
    material = sum(
        (
            Decimal(str(line.grams)) * Decimal(str(line.cost_per_kg)) / 1000
            for line in estimate.materials
        ),
        start=Decimal(0),
    )
    amortization = maintenance = electricity = extra = Decimal(0)
    if estimate.printer is not None:
        expenses = estimate.printer.expenses
        hours = Decimal(str(estimate.print_minutes)) / 60
        lifetime = Decimal(str(expenses.lifetime_hours))
        amortization = Decimal(str(expenses.acquisition_cost)) / lifetime * hours
        maintenance = Decimal(str(expenses.lifetime_maintenance)) / lifetime * hours
        electricity = (
            Decimal(str(expenses.power_watts))
            / 1000
            * hours
            * Decimal(str(expenses.electricity_per_kwh))
        )
        extra = hours * Decimal(str(expenses.extra_per_hour))
    production = (
        material + amortization + maintenance + electricity + extra
    ) / estimate.pieces
    price = production / (1 - Decimal(str(estimate.pricing.margin_percent)) / 100)
    tax = price * Decimal(str(estimate.pricing.tax_percent)) / 100

    def rounded(amount: Decimal) -> float:
        return float(amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

    return PieceCosts(
        material=rounded(material / estimate.pieces),
        amortization=rounded(amortization / estimate.pieces),
        maintenance=rounded(maintenance / estimate.pieces),
        electricity=rounded(electricity / estimate.pieces),
        extra=rounded(extra / estimate.pieces),
        production=rounded(production),
        price_before_tax=rounded(price),
        tax=rounded(tax),
        final_price=rounded(price + tax),
    )
