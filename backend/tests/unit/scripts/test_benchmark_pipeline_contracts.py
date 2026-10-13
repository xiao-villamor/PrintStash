"""Availability summaries distinguish policy refusals from execution failures."""

from __future__ import annotations

import pytest

from app.db.models.types import DerivativeKind
from app.schemas.jobs import DerivativeRead, DerivativeStatus
from scripts.benchmark_pipeline_contracts import (
    SampleOutcome,
    unavailable_derivative_outcome,
)


class TestUnavailableDerivativeOutcome:
    @pytest.mark.parametrize(
        "reason",
        [
            "worker_failed",
            "storage",
            "renderer_no_output",
            "future_producer_failure",
            None,
        ],
    )
    def test_classifies_operational_failures(self, reason):
        row = DerivativeRead(
            kind=DerivativeKind.THUMBNAIL,
            recipe_version=1,
            state=DerivativeStatus.FAILED,
            failure_reason=reason,
        )
        assert unavailable_derivative_outcome([row]) == SampleOutcome.FAILED

    @pytest.mark.parametrize(
        "reason",
        ["invalid_source", "unsupported_format", "no_geometry", "resource_limit"],
    )
    def test_preserves_input_refusals(self, reason):
        row = DerivativeRead(
            kind=DerivativeKind.METADATA,
            recipe_version=1,
            state=DerivativeStatus.FAILED,
            failure_reason=reason,
        )
        assert unavailable_derivative_outcome([row]) == SampleOutcome.REFUSED

    def test_preserves_native_timeout(self):
        row = DerivativeRead(
            kind=DerivativeKind.THUMBNAIL,
            recipe_version=1,
            state=DerivativeStatus.FAILED,
            failure_reason="timeout",
        )
        assert unavailable_derivative_outcome([row]) == SampleOutcome.TIMEOUT

    @pytest.mark.parametrize(
        "state",
        [
            DerivativeStatus.SKIPPED,
            DerivativeStatus.CANCELLED,
            DerivativeStatus.DISABLED,
        ],
    )
    def test_preserves_capability_unavailability(self, state):
        row = DerivativeRead(
            kind=DerivativeKind.THUMBNAIL, recipe_version=1, state=state
        )
        assert unavailable_derivative_outcome([row]) == SampleOutcome.REFUSED

    @pytest.mark.parametrize("other_reason", ["timeout", "invalid_source"])
    def test_prioritizes_operational_failure(self, other_reason):
        rows = [
            DerivativeRead(
                kind=DerivativeKind.METADATA,
                recipe_version=1,
                state=DerivativeStatus.FAILED,
                failure_reason=other_reason,
            ),
            DerivativeRead(
                kind=DerivativeKind.THUMBNAIL,
                recipe_version=1,
                state=DerivativeStatus.FAILED,
                failure_reason="worker_failed",
            ),
        ]
        assert unavailable_derivative_outcome(rows) == SampleOutcome.FAILED

    def test_prioritizes_native_timeout(self):
        rows = [
            DerivativeRead(
                kind=DerivativeKind.METADATA,
                recipe_version=1,
                state=DerivativeStatus.FAILED,
                failure_reason="resource_limit",
            ),
            DerivativeRead(
                kind=DerivativeKind.THUMBNAIL,
                recipe_version=1,
                state=DerivativeStatus.FAILED,
                failure_reason="timeout",
            ),
        ]
        assert unavailable_derivative_outcome(rows) == SampleOutcome.TIMEOUT

    @pytest.mark.parametrize(
        "state",
        [
            DerivativeStatus.READY,
            DerivativeStatus.PENDING,
            DerivativeStatus.RUNNING,
            DerivativeStatus.QUEUED,
        ],
    )
    def test_rejects_nonterminal_unavailability(self, state):
        row = DerivativeRead(
            kind=DerivativeKind.THUMBNAIL, recipe_version=1, state=state
        )
        with pytest.raises(ValueError, match="not terminal and unavailable"):
            unavailable_derivative_outcome([row])

    def test_rejects_empty_observations(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            unavailable_derivative_outcome([])
