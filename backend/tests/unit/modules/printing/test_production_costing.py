"""Costing adds expenses before dividing by yield and applying pricing rates."""

from datetime import timedelta

import pytest

from app.core.time import utcnow
from app.db.models import PrintJob, PrintJobState
from app.modules.printing.production_costing import finish_job_cost, piece_costs
from app.schemas.production_costing import (
    CostedMaterial,
    CostedPrinter,
    PricingRates,
    PrinterExpenses,
    ProductionEstimate,
)


class TestPieceCosts:
    def test_prorates_full_print_cost(self):
        estimate = ProductionEstimate(
            currency="EUR",
            pieces=2,
            print_minutes=120,
            materials=[
                CostedMaterial(name="PLA", grams=100, cost_per_kg=20),
                CostedMaterial(name="PETG", grams=50, cost_per_kg=30),
            ],
            printer=CostedPrinter(
                name="Voron",
                expenses=PrinterExpenses(
                    acquisition_cost=500,
                    lifetime_hours=1000,
                    lifetime_maintenance=100,
                    power_watts=200,
                    electricity_per_kwh=0.25,
                    extra_per_hour=1,
                ),
            ),
            pricing=PricingRates(margin_percent=20, tax_percent=10),
        )

        costs = piece_costs(estimate)

        assert costs.model_dump() == {
            "material": 1.75,
            "amortization": 0.5,
            "maintenance": 0.1,
            "electricity": 0.05,
            "extra": 1.0,
            "production": 3.4,
            "price_before_tax": 4.25,
            "tax": 0.425,
            "final_price": 4.675,
        }

    @pytest.mark.parametrize("grams,expected", [(0, 0), (0.05, 0.0001)])
    def test_prices_material_only_at_four_decimal_precision(self, grams, expected):
        estimate = ProductionEstimate(
            currency="USD",
            pieces=1,
            print_minutes=0,
            materials=[CostedMaterial(name="PLA", grams=grams, cost_per_kg=1)],
            printer=None,
            pricing=PricingRates(margin_percent=0, tax_percent=0),
        )

        assert piece_costs(estimate).final_price == expected


class TestStoppedRunCost:
    def test_estimates_only_the_observed_fraction_of_material(self):
        start = utcnow()
        job = PrintJob(
            file_id=1,
            model_id=1,
            remote_filename="hook.gcode",
            state=PrintJobState.FAILED,
            started_at=start,
            finished_at=start + timedelta(minutes=30),
            progress=0.25,
            production_rates={
                "currency": "EUR",
                "machine_per_hour": 2,
                "material_per_kg": 20,
                "slicer_seconds": 7200,
                "slicer_grams": 200,
            },
        )
        finish_job_cost(job)
        assert job.production_cost == {
            "currency": "EUR",
            "material": 1,
            "machine": 1,
            "total": 2,
            "estimated": True,
        }

    def test_omits_the_material_cost_when_stop_usage_is_unknown(self):
        job = PrintJob(
            file_id=1,
            model_id=1,
            remote_filename="hook.gcode",
            state=PrintJobState.CANCELLED,
            actual_duration_s=900,
            production_rates={
                "currency": "EUR",
                "machine_per_hour": 2,
                "material_per_kg": 20,
                "slicer_seconds": 7200,
                "slicer_grams": 200,
            },
        )
        finish_job_cost(job)
        assert job.production_cost == {
            "currency": "EUR",
            "material": None,
            "machine": 0.5,
            "total": 0.5,
            "estimated": False,
        }
