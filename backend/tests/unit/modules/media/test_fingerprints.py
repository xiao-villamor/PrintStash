"""Fingerprint outcomes have a closed set of terminal states."""

import pytest

from app.modules.media.fingerprints import (
    FingerprintRecord,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_facts import FingerprintFailureCode


class TestFingerprintResult:
    @pytest.mark.parametrize(
        "state",
        [
            "ready",
            "partial",
            "failed",
            "unsupported",
            "unknown",
            "pending",
            "",
            None,
            True,
            1,
            [],
            {},
        ],
    )
    def test_rejects_a_state_outside_the_enum(self, state):
        with pytest.raises(TypeError, match="fingerprint_state"):
            FingerprintResult(state=state)

    @pytest.mark.parametrize(
        ("state", "records", "cause"),
        [
            (
                FingerprintResultState.READY,
                (FingerprintRecord(0, 1, {"recipe": {"complete_geometry": True}}, ()),),
                None,
            ),
            (
                FingerprintResultState.PARTIAL,
                (
                    FingerprintRecord(
                        0, 1, {"recipe": {"complete_geometry": False}, "keys": None}, ()
                    ),
                ),
                FingerprintFailureCode.SAMPLED_SOURCE,
            ),
            (FingerprintResultState.FAILED, (), FingerprintFailureCode.INVALID_SOURCE),
            (
                FingerprintResultState.UNSUPPORTED,
                (),
                FingerprintFailureCode.STEP_UNAVAILABLE,
            ),
        ],
    )
    def test_accepts_valid_terminal_outcome(self, state, records, cause):
        result = FingerprintResult(state, records, cause)
        assert (result.state, result.records, result.failure_code) == (
            state,
            records,
            cause,
        )

    @pytest.mark.parametrize(
        "code", ["made_up_failure", "invalid_source", True, 1, {}, []]
    )
    def test_rejects_failure_cause_outside_the_enum(self, code):
        with pytest.raises(TypeError, match="fingerprint_failure"):
            FingerprintResult(FingerprintResultState.FAILED, failure_code=code)

    @pytest.mark.parametrize(
        "state", [FingerprintResultState.READY, FingerprintResultState.PARTIAL]
    )
    def test_rejects_success_without_records(self, state):
        with pytest.raises(ValueError, match="fingerprint_records"):
            FingerprintResult(state)

    @pytest.mark.parametrize(
        "state", [FingerprintResultState.FAILED, FingerprintResultState.UNSUPPORTED]
    )
    def test_rejects_records_on_failure(self, state):
        record = FingerprintRecord(0, 1, {"recipe": {"complete_geometry": True}}, ())
        with pytest.raises(ValueError, match="fingerprint_records"):
            FingerprintResult(state, (record,), FingerprintFailureCode.INVALID_SOURCE)

    @pytest.mark.parametrize(
        "state", [FingerprintResultState.FAILED, FingerprintResultState.UNSUPPORTED]
    )
    def test_rejects_failure_without_cause(self, state):
        with pytest.raises(ValueError, match="fingerprint_failure"):
            FingerprintResult(state)

    def test_rejects_ready_with_failure_cause(self):
        record = FingerprintRecord(0, 1, {"recipe": {"complete_geometry": True}}, ())
        with pytest.raises(ValueError, match="fingerprint_failure"):
            FingerprintResult(
                FingerprintResultState.READY,
                (record,),
                FingerprintFailureCode.INVALID_SOURCE,
            )

    @pytest.mark.parametrize("cause", [None, FingerprintFailureCode.INVALID_SOURCE])
    def test_rejects_partial_without_sampling_cause(self, cause):
        record = FingerprintRecord(
            0, 1, {"recipe": {"complete_geometry": False}, "keys": None}, ()
        )
        with pytest.raises(ValueError, match="fingerprint_sampling"):
            FingerprintResult(FingerprintResultState.PARTIAL, (record,), cause)

    def test_rejects_partial_with_complete_recipe(self):
        record = FingerprintRecord(
            0, 1, {"recipe": {"complete_geometry": True}, "keys": None}, ()
        )
        with pytest.raises(ValueError, match="fingerprint_sampling"):
            FingerprintResult(
                FingerprintResultState.PARTIAL,
                (record,),
                FingerprintFailureCode.SAMPLED_SOURCE,
            )

    def test_rejects_duplicate_component_indices(self):
        whole = FingerprintRecord(0, 1, {"recipe": {"complete_geometry": True}}, ())
        component = FingerprintRecord(
            1,
            1,
            {"recipe": {"complete_geometry": True}},
            ({"resource_id": "1", "transform": []},),
        )
        with pytest.raises(ValueError, match="fingerprint_component"):
            FingerprintResult(
                FingerprintResultState.READY, (whole, component, component)
            )


class TestFingerprintRecord:
    @pytest.mark.parametrize(
        ("index", "count"), [(-1, 1), (True, 1), (0, 0), (0, True), (0, -1)]
    )
    def test_rejects_invalid_record_identity(self, index, count):
        with pytest.raises(ValueError, match="fingerprint_record"):
            FingerprintRecord(index, count, {"recipe": {}}, ())

    def test_rejects_empty_descriptor_values(self):
        with pytest.raises(ValueError, match="fingerprint_record"):
            FingerprintRecord(0, 1, {}, ())

    def test_rejects_component_instance_count_mismatch(self):
        with pytest.raises(ValueError, match="fingerprint_record"):
            FingerprintRecord(1, 2, {"recipe": {}}, ({"resource_id": "1"},))
