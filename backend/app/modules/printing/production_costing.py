"""Calculate reproducible production costs from saved input snapshots."""

from decimal import ROUND_HALF_UP, Decimal

from sqlmodel import Session, select

from app.core.time import ensure_utc
from app.db.models import (
    File,
    Metadata,
    Printer,
    PrinterProfile,
    PrintJob,
    PrintJobState,
)
from app.modules.administration import runtime_config
from app.modules.media.sliced_gcode import plate_metadata
from app.modules.printing.costing import cost_profiles, filament_rate_for_job
from app.schemas.plates import GcodePlateMetadata
from app.schemas.production_costing import (
    JobCostRates,
    JobProductionCost,
    PieceCosts,
    PrinterExpenses,
    ProductionEstimate,
)


def _machine_rates(
    expenses: PrinterExpenses,
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    lifetime = Decimal(str(expenses.lifetime_hours))
    return (
        Decimal(str(expenses.acquisition_cost)) / lifetime,
        Decimal(str(expenses.lifetime_maintenance)) / lifetime,
        Decimal(str(expenses.power_watts))
        / 1000
        * Decimal(str(expenses.electricity_per_kwh)),
        Decimal(str(expenses.extra_per_hour)),
    )


def _rounded(amount: Decimal) -> float:
    return float(amount.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


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
        hours = Decimal(str(estimate.print_minutes)) / 60
        acquisition_rate, maintenance_rate, electricity_rate, extra_rate = (
            _machine_rates(estimate.printer.expenses)
        )
        amortization = acquisition_rate * hours
        maintenance = maintenance_rate * hours
        electricity = electricity_rate * hours
        extra = extra_rate * hours
    production = (
        material + amortization + maintenance + electricity + extra
    ) / estimate.pieces
    price = production / (1 - Decimal(str(estimate.pricing.margin_percent)) / 100)
    tax = price * Decimal(str(estimate.pricing.tax_percent)) / 100
    return PieceCosts(
        material=_rounded(material / estimate.pieces),
        amortization=_rounded(amortization / estimate.pieces),
        maintenance=_rounded(maintenance / estimate.pieces),
        electricity=_rounded(electricity / estimate.pieces),
        extra=_rounded(extra / estimate.pieces),
        production=_rounded(production),
        price_before_tax=_rounded(price),
        tax=_rounded(tax),
        final_price=_rounded(price + tax),
    )


def launch_job_cost(
    session: Session, printer: Printer, job: PrintJob, file: File | None
) -> None:
    job.production_rates, job.production_cost = job_cost_snapshot(
        session, printer, job, file
    )


def job_cost_snapshot(
    session: Session, printer: Printer, job: PrintJob, file: File | None
) -> tuple[dict | None, dict | None]:
    if printer.cost_profile_id is None:
        return None, None
    profile = session.get(PrinterProfile, printer.cost_profile_id)
    if profile is None or profile.expenses is None:
        return None, None
    metadata: Metadata | GcodePlateMetadata | None
    if job.plate_index is not None and file is not None:
        metadata = next(
            (
                plate.metadata
                for plate in plate_metadata(session, file)
                if plate.number == job.plate_index
            ),
            None,
        )
    else:
        metadata = session.exec(
            select(Metadata).where(Metadata.file_id == job.file_id)
        ).first()
    rate = filament_rate_for_job(
        cost_profiles(session), metadata, job.spool_filament_id
    )
    weight = metadata.filament_weight_g if metadata is not None else None
    if (
        rate is None
        and metadata is not None
        and metadata.filament_cost is not None
        and weight
    ):
        rate = metadata.filament_cost * 1000 / weight
    rates = JobCostRates(
        currency=runtime_config.currency(session),
        machine_per_hour=float(
            sum(_machine_rates(PrinterExpenses.model_validate(profile.expenses)))
        ),
        material_per_kg=rate,
        slicer_seconds=metadata.estimated_time_s if metadata is not None else None,
        slicer_grams=weight,
    )
    return (
        rates.model_dump(),
        _job_receipt(rates, rates.slicer_seconds, rates.slicer_grams, estimated=True),
    )


def _job_receipt(
    rates: JobCostRates, seconds: int | None, grams: float | None, *, estimated: bool
) -> dict | None:
    machine = (
        Decimal(seconds) / 3600 * Decimal(str(rates.machine_per_hour))
        if seconds is not None
        else None
    )
    material = (
        Decimal(str(grams)) * Decimal(str(rates.material_per_kg)) / 1000
        if grams is not None and rates.material_per_kg is not None
        else None
    )
    if machine is None and material is None:
        return None
    return JobProductionCost(
        currency=rates.currency,
        machine=_rounded(machine) if machine is not None else None,
        material=_rounded(material) if material is not None else None,
        total=_rounded((machine or Decimal(0)) + (material or Decimal(0))),
        estimated=estimated,
    ).model_dump()


def finish_job_cost(job: PrintJob) -> None:
    if job.production_rates is None:
        return
    rates = JobCostRates.model_validate(job.production_rates)
    seconds = job.actual_duration_s
    grams = job.filament_used_g
    estimated_time = seconds is None
    estimated_material = grams is None
    if seconds is None:
        if job.state is PrintJobState.COMPLETED and rates.slicer_seconds is not None:
            seconds = rates.slicer_seconds
        elif job.started_at is not None and job.finished_at is not None:
            elapsed = ensure_utc(job.finished_at) - ensure_utc(job.started_at)
            seconds = max(0, int(elapsed.total_seconds()))
    if grams is None and rates.slicer_grams is not None:
        if job.state is PrintJobState.COMPLETED:
            grams = rates.slicer_grams
        elif 0 < job.progress < 1:
            grams = rates.slicer_grams * job.progress
    job.production_cost = _job_receipt(
        rates,
        seconds,
        grams,
        estimated=(estimated_time and seconds is not None)
        or (
            estimated_material
            and grams is not None
            and rates.material_per_kg is not None
        ),
    )
