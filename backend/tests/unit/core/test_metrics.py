"""Prometheus labels are an unbounded-cardinality hazard and a leak channel.

A `/metrics` endpoint is scraped by something outside PrintStash and often kept
for a year, so every label value written here is effectively published. Two
distinct things go wrong when a label is taken from a caller verbatim.

The first is disclosure: capture operations are driven by URLs the *user* pasted,
and a provider download URL is signed — the credential is in the query string. A
label built from one puts a working credential into a monitoring system that has
no idea it is holding a secret, and that nobody thinks to rotate.

The second is cardinality: Prometheus creates one time series per distinct label
combination and never forgets it. A label that can take an arbitrary value turns
one metric into millions of series and takes the scrape target down with it.

Both are prevented the same way — a **closed allowlist per label**, with
everything unrecognised folded to a fixed placeholder. That is why these tests
assert on the rendered exposition text rather than on the counter objects: the
rendered text is what actually leaves the process.

Every recorder is also *best-effort*: it swallows its own failures, because a
broken metric must never fail the request, job, or dispatch it was measuring.
"""

from __future__ import annotations

import asyncio
from typing import cast

import pytest
from prometheus_client import generate_latest

from app.core import metrics
from app.core.metrics import (
    record_capture_operation,
    record_fleet_dispatch,
    record_ingestion_terminal,
    registry,
    set_ingestion_stuck_jobs,
)
from app.db.session import SessionFactory
from app.services import import_resolvers
from app.services.capture_provider_connections import (
    ProviderIdentity,
    ProviderModelMetadata,
)

# A URL shaped like a real signed provider download: the token *is* the
# credential, so it must never appear in the exposition text.
SIGNED_URL = "https://evil.test/a?token=secret-user-model-file"
MMF_PAGE_URL = "https://www.myminifactory.com/object/1"


def _exposition() -> str:
    return generate_latest(registry).decode()


def _sample(name: str, labels: dict[str, str]) -> float:
    """One metric's current value, or 0.0 before its first observation.

    The registry is process-wide and other tests write to it, so a test that
    cares about a delta reads this before and after rather than asserting an
    absolute count.
    """
    return registry.get_sample_value(name, labels) or 0.0


class _NoopSessionFactory:
    """Stands in for the session factory; the resolver only needs the context."""

    def scoped_session(self):
        class _Scope:
            def __enter__(self):
                return object()

            def __exit__(self, *_args: object) -> bool:
                return False

        return _Scope()


class TestRecordCaptureOperation:
    def test_records_a_capture_with_both_of_its_labels(self) -> None:
        record_capture_operation("printables", "provider_api", "success", 0.5)

        assert (
            'outcome="success",provider="printables",transport="provider_api"'
            in _exposition()
        )

    def test_counts_uploaded_bytes_for_the_provider(self) -> None:
        record_capture_operation(
            "thingiverse", "browser_upload", "success", 0.1, uploaded_bytes=7
        )

        assert 'capture_uploaded_bytes_total{provider="thingiverse"}' in _exposition()

    def test_records_an_error_category_alongside_the_outcome(self) -> None:
        record_capture_operation(
            "cults",
            "provider_api",
            "required",
            0,
            error_category="user_file_required",
        )

        assert 'category="user_file_required",provider="cults"' in _exposition()

    def test_folds_an_unknown_provider_to_a_fixed_placeholder(self) -> None:
        record_capture_operation(SIGNED_URL, "provider_api", "success", 0.1)

        # One series for every URL a user ever pastes would be unbounded
        # cardinality; `unknown` is one series.
        assert 'provider="unknown"' in _exposition()

    def test_folds_an_unknown_transport_to_a_fixed_placeholder(self) -> None:
        record_capture_operation("printables", SIGNED_URL, "success", 0.1)

        assert 'transport="unknown"' in _exposition()

    def test_folds_an_unknown_outcome_to_error(self) -> None:
        record_capture_operation("printables", "provider_api", SIGNED_URL, 0.1)

        # An unrecognised outcome is a bug somewhere upstream, so it is recorded
        # as a failure rather than quietly counted as a success.
        assert 'outcome="error",provider="printables"' in _exposition()

    def test_folds_an_unknown_error_category_to_a_fixed_placeholder(self) -> None:
        record_capture_operation(
            "printables", "provider_api", "error", 0.1, error_category=SIGNED_URL
        )

        assert 'category="unknown",provider="printables"' in _exposition()

    def test_never_publishes_a_credential_bearing_value(self) -> None:
        record_capture_operation(
            SIGNED_URL,
            SIGNED_URL,
            SIGNED_URL,
            0.1,
            uploaded_bytes=7,
            error_category=SIGNED_URL,
        )

        text = _exposition()
        # Every label position at once: a signed URL reaching any one of them
        # publishes a working credential to whoever scrapes /metrics.
        assert SIGNED_URL not in text
        assert "secret-user-model-file" not in text

    def test_records_a_negative_duration_as_zero(self) -> None:
        labels = {
            "provider": "printables",
            "transport": "provider_api",
            "outcome": "success",
        }
        before = _sample("printstash_capture_operation_duration_seconds_sum", labels)

        record_capture_operation("printables", "provider_api", "success", -5.0)

        # A clock that went backwards mid-operation must not drag a histogram
        # sum downward — it only ever counts upward, so the sum would be wrong
        # for the lifetime of the process.
        after = _sample("printstash_capture_operation_duration_seconds_sum", labels)
        assert after == before

    def test_does_not_count_bytes_when_none_were_uploaded(self) -> None:
        labels = {"provider": "makerworld"}
        before = _sample("printstash_capture_uploaded_bytes_total", labels)

        record_capture_operation(
            "makerworld", "upload_slots", "success", 0.1, uploaded_bytes=0
        )

        # A metadata-only capture transferred nothing; counting a zero would
        # make "bytes per capture" dashboards read as if it had.
        assert _sample("printstash_capture_uploaded_bytes_total", labels) == before

    def test_swallows_its_own_failure_rather_than_failing_the_capture(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Broken:
            def labels(self, *_args: object, **_kwargs: object):
                raise RuntimeError("registry unavailable")

        monkeypatch.setattr(metrics, "capture_operations", _Broken())

        # Telemetry is never worth failing the operation it measures.
        record_capture_operation("printables", "provider_api", "success", 0.1)


class TestObserveRequest:
    def test_records_a_completed_request(self) -> None:
        metrics.observe_request("GET", "/api/v1/models", 200, 0.25)

        assert 'method="GET"' in _exposition()

    def test_swallows_its_own_failure_rather_than_failing_the_request(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Broken:
            def labels(self, **_kwargs: object):
                raise RuntimeError("registry unavailable")

        monkeypatch.setattr(metrics, "http_request_duration", _Broken())

        metrics.observe_request("GET", "/api/v1/models", 200, 0.25)


class TestRecordIngestionTerminal:
    def test_records_a_terminal_job_with_both_of_its_labels(self) -> None:
        record_ingestion_terminal("upload", "completed", 1.5)

        assert 'kind="upload"' in _exposition()

    def test_records_a_negative_duration_as_zero(self) -> None:
        record_ingestion_terminal("upload", "failed", -1.0)

        assert 'result="failed"' in _exposition()

    def test_swallows_its_own_failure_rather_than_failing_the_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Broken:
            def labels(self, **_kwargs: object):
                raise RuntimeError("registry unavailable")

        monkeypatch.setattr(metrics, "ingestion_jobs", _Broken())

        record_ingestion_terminal("upload", "completed", 1.5)


class TestSetIngestionStuckJobs:
    def test_reports_the_stuck_job_count(self) -> None:
        set_ingestion_stuck_jobs(3)

        assert "ingestion_stuck_jobs 3.0" in _exposition()

    def test_reports_a_negative_count_as_zero(self) -> None:
        set_ingestion_stuck_jobs(-1)

        # A negative "stuck jobs" reading would fire or silence an alert on a
        # value that cannot exist.
        assert "ingestion_stuck_jobs 0.0" in _exposition()

    def test_swallows_its_own_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class _Broken:
            def set(self, _value: object) -> None:
                raise RuntimeError("registry unavailable")

        monkeypatch.setattr(metrics, "ingestion_stuck_jobs", _Broken())

        set_ingestion_stuck_jobs(3)


class TestRecordFleetDispatch:
    def test_records_a_dispatch_outcome(self) -> None:
        record_fleet_dispatch("queued")

        assert 'fleet_dispatches_total{outcome="queued"}' in _exposition()

    def test_swallows_its_own_failure_rather_than_failing_dispatch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _Broken:
            def labels(self, **_kwargs: object):
                raise RuntimeError("registry unavailable")

        monkeypatch.setattr(metrics, "fleet_dispatches", _Broken())

        record_fleet_dispatch("queued")


class TestResolverInstrumentation:
    """One observation per resolve, with the provider's real name attached.

    This is the seam where the label vocabulary meets a real caller: the
    resolver runs on a user-supplied URL, and it must emit exactly one bounded
    observation naming the provider it actually talked to.
    """

    def test_emits_one_bounded_success_observation(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import_resolvers._provider_metadata_cache.clear()

        async def metadata(*_args: object) -> ProviderModelMetadata:
            # The identity must bind to the submitted page: the resolver proves
            # the response describes the URL the user pasted before it trusts
            # anything else in it, so a metadata stand-in without a canonical
            # URL is rejected as a changed contract rather than measured.
            return ProviderModelMetadata(
                "1",
                "Widget",
                None,
                None,
                None,
                (),
                (),
                ProviderIdentity(provider_id="1", canonical_url=MMF_PAGE_URL),
            )

        monkeypatch.setattr(
            import_resolvers.provider_connections,
            "fetch_mmf_model_metadata",
            metadata,
        )

        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                MMF_PAGE_URL,
                import_resolvers.ProviderResolutionContext(
                    1, cast(SessionFactory, _NoopSessionFactory())
                ),
            )
        )

        assert (
            'outcome="success",provider="myminifactory",transport="provider_api"'
            in _exposition()
        )
