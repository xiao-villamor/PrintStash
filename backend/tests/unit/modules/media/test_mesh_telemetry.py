"""Phase telemetry is bounded data, never an unvalidated child metric stream."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from app.modules.media.mesh_telemetry import (
    MeshPhase,
    NativeFallback,
    PhaseOutcome,
    PhaseRecorder,
    PhaseStats,
    ReaderChoice,
    SupervisionStats,
    ThreeMFReader,
    WorkerExitCause,
    decode_phase_stats,
    encode_phase_stats,
)


@pytest.fixture
def stats() -> PhaseStats:
    return PhaseStats(MeshPhase.LOAD, 100, 684, None, 12, PhaseOutcome.COMPLETED)


class TestPhaseStats:
    def test_round_trips_known_costs(self, stats: PhaseStats) -> None:
        assert decode_phase_stats(encode_phase_stats((stats,))) == (stats,)

    @pytest.mark.parametrize(
        "field",
        ["elapsed_ns", "input_bytes", "output_bytes", "triangle_count"],
        ids=str,
    )
    @pytest.mark.parametrize(
        "invalid",
        [-1, True, 1.5, float("nan"), 2**63],
        ids=["negative", "boolean", "float", "nan", "overflow"],
    )
    def test_rejects_invalid_counter(
        self, stats: PhaseStats, field: str, invalid: object
    ) -> None:
        stage = {**asdict(stats), field: invalid}

        with pytest.raises(ValueError, match="invalid phase counter|Out of range"):
            decode_phase_stats({"version": 2, "stages": [stage]})

    @pytest.mark.parametrize(
        ("field", "value"),
        [("phase", "private-file-path"), ("outcome", "invented")],
        ids=["phase", "outcome"],
    )
    def test_rejects_unknown_labels(
        self, stats: PhaseStats, field: str, value: str
    ) -> None:
        with pytest.raises(ValueError, match="not a valid"):
            decode_phase_stats(
                {"version": 2, "stages": [{**asdict(stats), field: value}]}
            )

    def test_rejects_duplicate_stage(self, stats: PhaseStats) -> None:
        with pytest.raises(ValueError, match="duplicate phase stats"):
            decode_phase_stats({"version": 2, "stages": [asdict(stats), asdict(stats)]})

    def test_rejects_unbounded_stage_count(self, stats: PhaseStats) -> None:
        with pytest.raises(ValueError, match="invalid phase stats count"):
            decode_phase_stats(
                {"version": 2, "stages": [asdict(stats)] * (len(MeshPhase) + 1)}
            )

    def test_rejects_oversized_stats(self, stats: PhaseStats) -> None:
        with pytest.raises(ValueError, match="phase stats too large"):
            decode_phase_stats(
                {"version": 2, "stages": [{**asdict(stats), "phase": "x" * 4096}]}
            )

    @pytest.mark.parametrize(
        "version", [1, 3, True, "2"], ids=["old", "future", "boolean", "string"]
    )
    def test_rejects_unsupported_version(self, version: object) -> None:
        with pytest.raises(ValueError, match="unsupported phase stats version"):
            decode_phase_stats({"version": version, "stages": []})

    def test_rejects_missing_stage_fields(self) -> None:
        with pytest.raises(ValueError, match="invalid phase stats fields"):
            decode_phase_stats({"version": 2, "stages": [{}]})

    @pytest.mark.parametrize(
        "payload",
        [
            None,
            {},
            {"version": 2, "stages": {}},
            {"version": 2, "stages": [], "extra": 1},
        ],
        ids=["absent", "empty", "wrong-stage-type", "extra-field"],
    )
    def test_rejects_malformed_envelope(self, payload: object) -> None:
        with pytest.raises(ValueError, match="invalid phase stats"):
            decode_phase_stats(payload)


class TestReaderChoice:
    @pytest.mark.parametrize(
        "choice",
        [
            ReaderChoice(ThreeMFReader.LIB3MF),
            ReaderChoice(ThreeMFReader.XML, NativeFallback.MIXED_UNITS),
        ],
        ids=["lib3mf", "xml-fallback"],
    )
    def test_round_trips_on_the_load_stage(
        self, stats: PhaseStats, choice: ReaderChoice
    ) -> None:
        loaded = PhaseStats(
            MeshPhase.LOAD, 100, 684, None, 12, PhaseOutcome.COMPLETED, choice
        )

        assert decode_phase_stats(encode_phase_stats((loaded,))) == (loaded,)

    @pytest.mark.parametrize(
        ("reader", "fallback"),
        [
            (ThreeMFReader.XML, None),
            (ThreeMFReader.LIB3MF, NativeFallback.NOT_OPC),
        ],
        ids=["xml-without-reason", "lib3mf-with-reason"],
    )
    def test_refuses_a_reason_that_does_not_match_the_reader(
        self, reader: ThreeMFReader, fallback: NativeFallback | None
    ) -> None:
        with pytest.raises(ValueError, match="exactly one fallback reason"):
            ReaderChoice(reader, fallback)

    def test_refuses_a_reader_on_another_stage(self) -> None:
        with pytest.raises(ValueError, match="only the load stage"):
            PhaseStats(
                MeshPhase.RENDER,
                1,
                None,
                None,
                None,
                PhaseOutcome.COMPLETED,
                ReaderChoice(ThreeMFReader.LIB3MF),
            )

    @pytest.mark.parametrize(
        "reader",
        [
            {"reader": "xml", "fallback": None},
            {"reader": "xml", "fallback": "/private/path"},
            {"reader": "invented", "fallback": None},
            {"reader": "lib3mf"},
        ],
        ids=["xml-without-reason", "unknown-reason", "unknown-reader", "missing"],
    )
    def test_decoding_refuses_an_invalid_reader(
        self, stats: PhaseStats, reader: object
    ) -> None:
        stage = {**asdict(stats), "reader": reader}

        with pytest.raises(ValueError):
            decode_phase_stats({"version": 2, "stages": [stage]})


class TestPhaseRecorder:
    def test_keeps_the_reader_of_a_repeated_load(self) -> None:
        choice = ReaderChoice(ThreeMFReader.XML, NativeFallback.SECOND_MODEL)
        recorder = PhaseRecorder()
        recorder.start(MeshPhase.LOAD)
        recorder.finish(reader=choice)
        recorder.start(MeshPhase.LOAD)
        recorder.finish()

        (stat,) = recorder.snapshot()

        assert stat.reader == choice

    def test_aggregates_repeated_stage(self) -> None:
        recorder = PhaseRecorder()
        recorder.start(MeshPhase.LOAD, input_bytes=100)
        recorder.finish(output_bytes=50)
        recorder.start(MeshPhase.LOAD, input_bytes=200)
        recorder.finish(output_bytes=75, outcome=PhaseOutcome.FAILED)

        (stat,) = recorder.snapshot()

        assert stat.input_bytes == 300
        assert stat.output_bytes == 125
        assert stat.elapsed_ns > 0
        assert stat.outcome is PhaseOutcome.FAILED

    def test_preserves_unknown_sizes(self) -> None:
        recorder = PhaseRecorder()
        recorder.start(MeshPhase.LOAD)
        recorder.finish()
        recorder.start(MeshPhase.LOAD, input_bytes=200)
        recorder.finish(output_bytes=75)

        (stat,) = recorder.snapshot()

        assert stat.input_bytes is None
        assert stat.output_bytes is None

    def test_rejects_overlapping_stage(self) -> None:
        recorder = PhaseRecorder()
        recorder.start(MeshPhase.LOAD)

        with pytest.raises(RuntimeError, match="overlapping mesh stages"):
            recorder.start(MeshPhase.RENDER)

    def test_rejects_unfinished_snapshot(self) -> None:
        recorder = PhaseRecorder()
        recorder.start(MeshPhase.LOAD)

        with pytest.raises(RuntimeError, match="mesh stage still active"):
            recorder.snapshot()

    def test_rejects_finish_without_active_stage(self) -> None:
        with pytest.raises(RuntimeError, match="no active mesh stage"):
            PhaseRecorder().finish()

    def test_records_interrupted_stage(self) -> None:
        recorder = PhaseRecorder()
        recorder.start(MeshPhase.LOAD)

        recorder.fail_active()

        assert recorder.snapshot()[0].outcome is PhaseOutcome.FAILED

    def test_leaves_idle_recorder_empty(self) -> None:
        recorder = PhaseRecorder()

        recorder.fail_active()

        assert recorder.snapshot() == ()


class TestSupervisionStats:
    @pytest.mark.parametrize(
        "execution_id", ["", "a" * 33, "g" * 32], ids=["empty", "too-long", "not-hex"]
    )
    def test_rejects_invalid_execution_id(self, execution_id: str) -> None:
        with pytest.raises(ValueError, match="invalid mesh execution id"):
            SupervisionStats(execution_id, 0, None, 0, WorkerExitCause.SPAWN_FAILED)
