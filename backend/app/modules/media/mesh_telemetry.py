"""Bounded mesh stage statistics and parent-owned native process costs.

Stages are sequential orchestration spans, inclusive of nested native work.
They exclude gaps and must not be added to the parent's end-to-end duration.
No child progress frames exist: a terminated child's active phase is unknown.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from enum import StrEnum


class MeshPhase(StrEnum):
    ADMISSION = "admission"
    EMBEDDED = "embedded"
    LOAD = "load"
    MEASUREMENTS = "measurements"
    FINGERPRINT = "fingerprint"
    RENDER = "render"
    STREAMING = "streaming"
    FALLBACK = "fallback"


class PhaseOutcome(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class WorkerExitCause(StrEnum):
    EXITED_ZERO = "exited_zero"
    EXITED_NONZERO = "exited_nonzero"
    DEADLINE = "deadline"
    MEMORY_LIMIT = "memory_limit"
    SIGKILL = "sigkill"
    REPLY_LIMIT = "reply_limit"
    CANCELLED = "cancelled"
    SPAWN_FAILED = "spawn_failed"
    SUPERVISION_FAILED = "supervision_failed"


@dataclass(frozen=True)
class PhaseStats:
    phase: MeshPhase
    elapsed_ns: int
    input_bytes: int | None
    output_bytes: int | None
    triangle_count: int | None
    outcome: PhaseOutcome


@dataclass(frozen=True)
class SupervisionStats:
    execution_id: str
    elapsed_ns: int
    peak_tree_rss_bytes: int | None
    reply_bytes: int
    exit_cause: WorkerExitCause
    # No phase can be established from the single terminal reply protocol.
    active_phase: MeshPhase | None = None

    def __post_init__(self) -> None:
        if re.fullmatch(r"[0-9a-f]{32}", self.execution_id) is None:
            raise ValueError("invalid mesh execution id")


@dataclass(frozen=True)
class SupervisedReply:
    payload: bytes
    stats: SupervisionStats


MAX_PHASE_STATS_BYTES = 4096
_MAX_COUNTER = 2**63 - 1


def _counter(value: object) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_COUNTER:
        raise ValueError("invalid phase counter")
    return value


def _optional_counter(value: object) -> int | None:
    return None if value is None else _counter(value)


def encode_phase_stats(stats: tuple[PhaseStats, ...]) -> dict[str, object]:
    payload = {"version": 1, "stages": [asdict(stat) for stat in stats]}
    # The same strict boundary applies to locally generated frames.
    decode_phase_stats(payload)
    return payload


def decode_phase_stats(raw: object) -> tuple[PhaseStats, ...]:
    if not isinstance(raw, dict) or set(raw) != {"version", "stages"}:
        raise ValueError("invalid phase stats envelope")
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise ValueError("unsupported phase stats version")
    stages = raw["stages"]
    if not isinstance(stages, list) or len(stages) > len(MeshPhase):
        raise ValueError("invalid phase stats count")
    if len(json.dumps(raw, allow_nan=False).encode()) > MAX_PHASE_STATS_BYTES:
        raise ValueError("phase stats too large")
    result: list[PhaseStats] = []
    seen: set[MeshPhase] = set()
    for stage in stages:
        if not isinstance(stage, dict) or set(stage) != {
            "phase",
            "elapsed_ns",
            "input_bytes",
            "output_bytes",
            "triangle_count",
            "outcome",
        }:
            raise ValueError("invalid phase stats fields")
        phase = MeshPhase(stage["phase"])
        if phase in seen:
            raise ValueError("duplicate phase stats")
        seen.add(phase)
        result.append(
            PhaseStats(
                phase=phase,
                elapsed_ns=_counter(stage["elapsed_ns"]),
                input_bytes=_optional_counter(stage["input_bytes"]),
                output_bytes=_optional_counter(stage["output_bytes"]),
                triangle_count=_optional_counter(stage["triangle_count"]),
                outcome=PhaseOutcome(stage["outcome"]),
            )
        )
    return tuple(result)


class PhaseRecorder:
    """One bounded aggregate per sequential stage; callers state known sizes."""

    def __init__(self) -> None:
        self._stats: dict[MeshPhase, PhaseStats] = {}
        self._active: tuple[MeshPhase, int, int | None] | None = None

    def start(self, phase: MeshPhase, *, input_bytes: int | None = None) -> None:
        if self._active is not None:
            raise RuntimeError("overlapping mesh stages")
        self._active = (phase, time.monotonic_ns(), input_bytes)

    def finish(
        self,
        *,
        output_bytes: int | None = None,
        triangle_count: int | None = None,
        outcome: PhaseOutcome = PhaseOutcome.COMPLETED,
    ) -> None:
        if self._active is None:
            raise RuntimeError("no active mesh stage")
        phase, started, input_bytes = self._active
        self._active = None
        current = PhaseStats(
            phase,
            time.monotonic_ns() - started,
            input_bytes,
            output_bytes,
            triangle_count,
            outcome,
        )
        previous = self._stats.get(phase)
        if previous is not None:
            # Re-entry is aggregated without an unbounded event list. Unknown
            # byte counts stay unknown instead of pretending to be zero.
            current = PhaseStats(
                phase,
                previous.elapsed_ns + current.elapsed_ns,
                None
                if previous.input_bytes is None or input_bytes is None
                else previous.input_bytes + input_bytes,
                None
                if previous.output_bytes is None or output_bytes is None
                else previous.output_bytes + output_bytes,
                triangle_count,
                PhaseOutcome.FAILED
                if PhaseOutcome.FAILED in (previous.outcome, outcome)
                else outcome,
            )
        self._stats[phase] = current

    def fail_active(self) -> None:
        if self._active is not None:
            self.finish(outcome=PhaseOutcome.FAILED)

    def snapshot(self) -> tuple[PhaseStats, ...]:
        if self._active is not None:
            raise RuntimeError("mesh stage still active")
        return tuple(self._stats.values())
