"""Moonraker diagnostics and bed measurement for the printer workbench."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator


class ConsoleCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    command: str = Field(min_length=1, max_length=2048, pattern=r"^[^\x00-\x1f\x7f]+$")


class ConsoleEntry(BaseModel):
    time: FiniteFloat
    type: Literal["command", "response"]
    message: str


class RecordedHeaterSample(BaseModel):
    age_ms: Annotated[
        int, Field(ge=0, description="Nominal age relative to the newest store slot.")
    ]
    temperature: FiniteFloat | None
    target: FiniteFloat | None


class PrinterHeaterHistory(BaseModel):
    sample_interval_ms: Annotated[
        Literal[1000],
        Field(description="Moonraker's nominal cadence. The store omits timestamps."),
    ]
    extruder: list[RecordedHeaterSample]
    heater_bed: list[RecordedHeaterSample]


class UnconfiguredBedMesh(BaseModel):
    state: Literal["unconfigured"]


class InactiveBedMesh(BaseModel):
    state: Literal["inactive"]


class ActiveBedMesh(BaseModel):
    state: Literal["active"]
    profile_name: str | None
    mesh_min: tuple[FiniteFloat, FiniteFloat]
    mesh_max: tuple[FiniteFloat, FiniteFloat]
    probed_matrix: list[list[FiniteFloat]]
    variation_mm: FiniteFloat


PrinterBedMesh = Annotated[
    ActiveBedMesh | InactiveBedMesh | UnconfiguredBedMesh, Field(discriminator="state")
]


class BedMeasurementAvailability(BaseModel):
    supported: bool


class ConfirmBedMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def require_confirmation(cls, value: object) -> object:
        if value is not True:
            raise ValueError("bed measurement requires confirmation")
        return value
