"""Export failures cannot change mesh outcomes; records survive the text logger."""

from __future__ import annotations

import json

import pytest

from app.modules.media import mesh_observability
from app.modules.media.mesh_telemetry import (
    MeshPhase,
    PhaseOutcome,
    PhaseStats,
    SupervisionStats,
    WorkerExitCause,
)


@pytest.fixture
def supervision() -> SupervisionStats:
    return SupervisionStats("a" * 32, 1000, 1024, 10, WorkerExitCause.EXITED_ZERO)


class TestRecordSupervision:
    def test_keeps_costs_in_log_message(
        self, supervision: SupervisionStats, caplog: pytest.LogCaptureFixture
    ) -> None:
        mesh_observability.record_supervision(supervision)

        record = next(
            record
            for record in caplog.records
            if record.getMessage().startswith("mesh_supervision ")
        )
        payload = json.loads(record.getMessage().removeprefix("mesh_supervision "))
        assert payload["execution_id"] == supervision.execution_id
        assert payload["peak_tree_rss_bytes"] == 1024
        assert payload["elapsed_ns"] == 1000

    def test_contains_metrics_sink_failure(
        self,
        supervision: SupervisionStats,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def fail(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("metrics unavailable")

        monkeypatch.setattr(mesh_observability._worker_exits, "labels", fail)

        result = mesh_observability.record_supervision(supervision)

        assert result is None
        assert "failed to publish mesh supervision metrics" in caplog.text


class TestRecordPhases:
    def test_correlates_logged_phases(
        self, supervision: SupervisionStats, caplog: pytest.LogCaptureFixture
    ) -> None:
        phase = PhaseStats(MeshPhase.LOAD, 100, 12, None, 4, PhaseOutcome.COMPLETED)

        mesh_observability.record_phases((phase,), supervision)

        record = next(
            record
            for record in caplog.records
            if record.getMessage().startswith("mesh_phases ")
        )
        payload = json.loads(record.getMessage().removeprefix("mesh_phases "))
        assert payload["execution_id"] == supervision.execution_id
        assert payload["stages"][0]["output_bytes"] is None
        assert payload["stages"][0]["triangle_count"] == 4

    def test_contains_metrics_sink_failure(
        self,
        supervision: SupervisionStats,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        def fail(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("metrics unavailable")

        phase = PhaseStats(MeshPhase.LOAD, 100, 12, None, 4, PhaseOutcome.COMPLETED)
        monkeypatch.setattr(mesh_observability._phase_duration, "labels", fail)

        result = mesh_observability.record_phases((phase,), supervision)

        assert result is None
        assert "failed to publish mesh phase metrics" in caplog.text


class TestRecordAdmission:
    def test_contains_metrics_sink_failure(self, monkeypatch, caplog):
        from app.modules.media.mesh_telemetry import AdmissionOutcome, AdmissionStats

        def fail(*_args, **_kwargs):
            raise RuntimeError("metrics unavailable")

        monkeypatch.setattr(mesh_observability._admission_duration, "labels", fail)
        result = mesh_observability.record_admission(
            AdmissionStats(100, 1, 40, 2, 100, AdmissionOutcome.ADMITTED)
        )

        assert result is None
        assert "failed to publish native admission metrics" in caplog.text
