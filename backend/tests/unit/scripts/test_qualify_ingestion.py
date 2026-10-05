"""Qualification counts fresh usable Artifacts without disguising replay or refusal."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from scripts.benchmark_pipeline_contracts import SampleOutcome
from scripts.mesh_benchmark_corpus import build_contract_corpus


@pytest.fixture
def observation():
    from app.schemas.jobs import DerivativeStatus
    from scripts.qualify_ingestion import QualificationObservation

    return QualificationObservation(
        name="part.stl",
        input_sha256="a" * 64,
        input_bytes=100,
        sample_index=0,
        elapsed_ms=100,
        outcome=SampleOutcome.COMPLETED,
        reason=None,
        accepted_ms=1,
        artifact_observed_ms=2,
        metadata_observed_ms=3,
        thumbnail_visible_ms=4,
        metadata_state=DerivativeStatus.READY,
        thumbnail_state=DerivativeStatus.READY,
        file_id=1,
        source_preexisting=False,
        source_probe_ms=1,
        artifact_reused=False,
        output_bytes=10,
        output_sha256="b" * 64,
        output_format="WEBP",
        output_dimensions=(640, 480),
        original_verified=True,
        original_verification_ms=1,
        thumbnail_decoded=True,
    )


class TestVariant:
    def test_preserves_geometry_payload(self, tmp_path):
        name = "cube-binary.stl"
        from scripts.qualify_ingestion import variant

        corpus = tmp_path / "corpus"
        build_contract_corpus(corpus)
        source = corpus / name
        original = source.read_bytes()
        first = tmp_path / ("first" + source.suffix)
        second = tmp_path / ("second" + source.suffix)

        variant(source, first, 1)
        variant(source, second, 2)

        assert source.read_bytes() == original
        assert (
            hashlib.sha256(first.read_bytes()).digest()
            != hashlib.sha256(second.read_bytes()).digest()
        )
        assert first.read_bytes()[80:] == original[80:]
        assert second.read_bytes()[80:] == original[80:]

    def test_preserves_archive_members(self, tmp_path):
        import zipfile

        from scripts.qualify_ingestion import variant

        corpus = tmp_path / "corpus"
        build_contract_corpus(corpus)
        source = corpus / "cube-mm.3mf"
        original = source.read_bytes()
        target = tmp_path / "variant.3mf"
        with zipfile.ZipFile(source) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}

        variant(source, target, 1)

        with zipfile.ZipFile(target) as archive:
            actual = {name: archive.read(name) for name in archive.namelist()}
        assert actual == members
        assert target.read_bytes() != original
        assert source.read_bytes() == original


class TestSummarize:
    @pytest.mark.parametrize(
        "elapsed,count",
        [(7199, 1000), (7200, 999)],
        ids=["duration-short", "count-short"],
    )
    def test_requires_both_thresholds(self, observation, elapsed, count):
        from scripts.qualify_ingestion import summarize

        samples = [
            replace(observation, file_id=index + 1, input_sha256=f"{index:064x}")
            for index in range(count)
        ]

        result = summarize(samples, elapsed_seconds=elapsed)

        assert result["soak_thresholds_met"] is False

    def test_counts_distinct_usable_artifacts(self, observation):
        from scripts.qualify_ingestion import summarize

        samples = [
            observation,
            replace(observation, artifact_reused=True),
            replace(observation, file_id=2, original_verified=False),
            replace(observation, file_id=3, outcome=SampleOutcome.REFUSED),
        ]

        result = summarize(samples, elapsed_seconds=7200)

        assert result["distinct_usable_artifacts"] == 1
        assert result["observations"] == 4
        assert result["soak_thresholds_met"] is False

    def test_retains_every_outcome(self, observation):
        from scripts.qualify_ingestion import summarize

        samples = [
            observation,
            replace(observation, outcome=SampleOutcome.FAILED),
            replace(observation, outcome=SampleOutcome.REFUSED),
            replace(observation, outcome=SampleOutcome.TIMEOUT),
        ]

        result = summarize(samples, elapsed_seconds=7200)

        assert result["outcomes"] == {
            "completed": 1,
            "refused": 1,
            "failed": 1,
            "timeout": 1,
        }
        assert result["observations"] == 4


class TestQualificationCredits:
    def test_excludes_replay_that_creates_a_fresh_artifact(self, observation):
        from scripts.qualify_ingestion import ObservationPurpose, summarize

        faulty_replay = replace(
            observation, file_id=2, purpose=ObservationPurpose.REPLAY
        )
        result = summarize([observation, faulty_replay], elapsed_seconds=7200)

        assert result["distinct_usable_artifacts"] == 1
        assert result["purposes"] == {"fresh": 1, "replay": 1}

    def test_excludes_warmup_artifacts(self, observation):
        from scripts.qualify_ingestion import ObservationPurpose, summarize

        result = summarize(
            [replace(observation, purpose=ObservationPurpose.WARMUP)],
            elapsed_seconds=7200,
        )

        assert result["distinct_usable_artifacts"] == 0


class TestExpectedRefusal:
    @pytest.mark.parametrize(
        "outcome", list(SampleOutcome), ids=lambda item: item.value
    )
    def test_requires_exact_refusal(self, observation, outcome):
        from scripts.qualify_ingestion import expected_refusal

        assert expected_refusal(replace(observation, outcome=outcome)) is (
            outcome == SampleOutcome.REFUSED
        )


class TestHeartbeatSummary:
    def test_rejects_missing_heartbeat_samples(self):
        from scripts.qualify_ingestion import heartbeat_summary

        assert heartbeat_summary([])["under_50ms"] is False

    @pytest.mark.parametrize("lag", [50.0, 51.0], ids=["boundary", "over"])
    def test_rejects_lag_at_the_gate(self, lag):
        from scripts.qualify_ingestion import heartbeat_summary

        assert heartbeat_summary([lag])["under_50ms"] is False

    def test_accepts_lag_below_the_gate(self):
        from scripts.qualify_ingestion import heartbeat_summary

        result = heartbeat_summary([1.0, 49.0])

        assert result["under_50ms"] is True
        assert result["max_lag_ms"] == 49.0


class TestArchiveVerified:
    @pytest.mark.parametrize(
        "state,unchanged,entries",
        [
            pytest.param(
                "failed",
                True,
                [{"original_verified": True, "outcome": "completed"}],
                id="failed-batch",
            ),
            pytest.param(
                "completed",
                False,
                [{"original_verified": True, "outcome": "completed"}],
                id="changed-source",
            ),
            pytest.param("completed", True, [], id="empty-archive"),
            pytest.param(
                "completed",
                True,
                [{"original_verified": False, "outcome": "completed"}],
                id="unverified-original",
            ),
            pytest.param(
                "completed",
                True,
                [{"original_verified": True, "outcome": "failed"}],
                id="failed-entry",
            ),
            pytest.param(
                "completed",
                True,
                [{"original_verified": True, "outcome": "timeout"}],
                id="timeout-entry",
            ),
        ],
    )
    def test_rejects_incomplete_archive_evidence(self, state, unchanged, entries):
        from scripts.qualify_ingestion import archive_verified

        assert archive_verified(state, unchanged, entries) is False

    @pytest.mark.parametrize(
        "outcome", ["completed", "refused"], ids=["ready", "typed-refusal"]
    )
    def test_accepts_verified_terminal_sources(self, outcome):
        from scripts.qualify_ingestion import archive_verified

        assert (
            archive_verified(
                "completed", True, [{"original_verified": True, "outcome": outcome}]
            )
            is True
        )


class TestFreshIdentity:
    def test_excludes_preexisting_sources(self, observation):
        from scripts.qualify_ingestion import summarize

        result = summarize(
            [replace(observation, source_preexisting=True)], elapsed_seconds=7200
        )

        assert result["distinct_usable_artifacts"] == 0

    def test_counts_duplicate_source_hash_once(self, observation):
        from scripts.qualify_ingestion import summarize

        result = summarize(
            [observation, replace(observation, file_id=2)], elapsed_seconds=7200
        )

        assert result["distinct_usable_artifacts"] == 1


class TestDecodedPreview:
    def test_excludes_unverified_preview_pixels(self, observation):
        from scripts.qualify_ingestion import summarize

        result = summarize(
            [replace(observation, thumbnail_decoded=False)], elapsed_seconds=7200
        )

        assert result["distinct_usable_artifacts"] == 0


class TestHeartbeatPercentile:
    def test_gates_p95_while_retaining_outlier_maximum(self):
        from scripts.qualify_ingestion import heartbeat_summary

        result = heartbeat_summary([1.0] * 99 + [100.0])

        assert result["p95_lag_ms"] == 1.0
        assert result["max_lag_ms"] == 100.0
        assert result["under_50ms"] is True


class TestEffectiveCPUCapacity:
    @pytest.mark.parametrize(
        "affinity,quota,read,expected",
        [
            pytest.param(8, 3.5, True, 3.5, id="cgroup-quota"),
            pytest.param(2, 8.0, True, 2.0, id="affinity"),
            pytest.param(4, None, True, 4.0, id="known-unlimited-quota"),
            pytest.param(8, None, False, None, id="quota-unreadable"),
            pytest.param(None, 8.0, True, None, id="affinity-unreadable"),
        ],
    )
    def test_observes_cpu_limits_without_host_count_fallback(
        self, affinity, quota, read, expected
    ):
        from scripts.benchmark_environment import CgroupLimits
        from scripts.qualify_ingestion import effective_cpu_capacity

        assert (
            effective_cpu_capacity(affinity, CgroupLimits(quota, None, read, False))
            == expected
        )


class TestThroughputSummary:
    @pytest.mark.parametrize(
        "rate,passed",
        [
            pytest.param(2.49, False, id="below"),
            pytest.param(2.5, True, id="boundary"),
            pytest.param(3.0, True, id="above"),
        ],
    )
    def test_gates_full_flow_ratio_on_a_capable_host(self, rate, passed):
        from scripts.qualify_ingestion import throughput_summary

        result = throughput_summary(
            1.0,
            rate,
            effective_cpus=4,
            native_slots=4,
            native_memory_bytes=4 * 1024**3,
            worker_envelope_bytes=1024**3,
        )

        assert result["four_to_one_ratio"] == rate
        assert result["eligible"] is True
        assert result["gate_passed"] is passed

    @pytest.mark.parametrize(
        "cpu,slots,memory",
        [
            pytest.param(None, 4, 4, id="unknown-cpu"),
            pytest.param(3.9, 4, 4, id="insufficient-cpu"),
            pytest.param(4, 3, 4, id="insufficient-slots"),
            pytest.param(4, 4, 3, id="insufficient-memory"),
        ],
    )
    def test_reports_uncapable_host_as_unqualified(self, cpu, slots, memory):
        from scripts.qualify_ingestion import throughput_summary

        result = throughput_summary(
            1.0,
            4.0,
            effective_cpus=cpu,
            native_slots=slots,
            native_memory_bytes=memory * 1024**3,
            worker_envelope_bytes=1024**3,
        )

        assert result["four_to_one_ratio"] == 4.0
        assert result["eligible"] is False
        assert result["gate_passed"] is None
        assert result["qualification"] == "not_qualified_N/A"

    def test_rejects_missing_serial_useful_throughput(self):
        from scripts.qualify_ingestion import throughput_summary

        result = throughput_summary(
            0.0,
            4.0,
            effective_cpus=4,
            native_slots=4,
            native_memory_bytes=4 * 1024**3,
            worker_envelope_bytes=1024**3,
        )

        assert result["four_to_one_ratio"] is None
        assert result["gate_passed"] is False

    @pytest.mark.parametrize(
        "rate", [-1.0, float("nan"), float("inf")], ids=["negative", "nan", "infinite"]
    )
    def test_rejects_invalid_throughput_measurements(self, rate):
        from scripts.qualify_ingestion import throughput_summary

        with pytest.raises(ValueError, match="finite and nonnegative"):
            throughput_summary(
                rate,
                4.0,
                effective_cpus=4,
                native_slots=4,
                native_memory_bytes=4 * 1024**3,
                worker_envelope_bytes=1024**3,
            )


class TestSoakCorpus:
    def test_rotates_closed_geometry_at_distinct_sizes(self, tmp_path):
        import json
        import struct
        from collections import Counter

        from scripts.qualify_ingestion import build_soak_corpus

        build_contract_corpus(tmp_path)
        paths = build_soak_corpus(tmp_path)
        manifest = json.loads((tmp_path / "soak-manifest.json").read_text())["fixtures"]
        assert [entry["faces"] for entry in manifest] == [12, 12, 5004, 20004]
        assert (
            len({hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}) == 4
        )
        for path, expected in zip(paths, manifest, strict=True):
            payload = path.read_bytes()
            assert len(payload) == expected["input_bytes"]
            if path.suffix == ".3mf":
                continue
            facets = list(struct.iter_unpack("<12fH", payload[84:]))
            assert (
                struct.unpack("<I", payload[80:84])[0]
                == len(facets)
                == expected["faces"]
            )
            assert len(payload) == 84 + expected["faces"] * 50
            edges = Counter()
            points = []
            signed_volume = 0.0
            for facet in facets:
                a, b, c = [tuple(facet[index : index + 3]) for index in (3, 6, 9)]
                points.extend((a, b, c))
                for left, right in ((a, b), (b, c), (c, a)):
                    edges[tuple(sorted((left, right)))] += 1
                signed_volume += (
                    a[0] * (b[1] * c[2] - b[2] * c[1])
                    + a[1] * (b[2] * c[0] - b[0] * c[2])
                    + a[2] * (b[0] * c[1] - b[1] * c[0])
                ) / 6
            assert set(edges.values()) == {2}
            assert signed_volume == pytest.approx(expected["volume_mm3"])
            assert [
                max(point[axis] for point in points)
                - min(point[axis] for point in points)
                for axis in range(3)
            ] == expected["bbox_mm"]


class TestMalformedControl:
    def test_periodic_controls_have_distinct_identities(
        self, tmp_path, monkeypatch, observation
    ):
        import time
        from unittest.mock import MagicMock

        from scripts import benchmark_ingestion
        from scripts.qualify_ingestion import malformed_control, summarize

        identities = []
        controls = []

        def measure(client, source, identity, **kwargs):
            identities.append(identity)
            result = replace(
                observation,
                outcome=SampleOutcome.REFUSED,
                file_id=None,
                input_sha256=identity.input_sha256,
            )
            controls.append(result)
            return result

        monkeypatch.setattr(benchmark_ingestion, "measure_ingestion", measure)
        first = malformed_control(MagicMock(), tmp_path, time.monotonic() + 10, 50)
        second = malformed_control(MagicMock(), tmp_path, time.monotonic() + 10, 100)

        assert first["expected_refusal_observed"] is True
        assert second["expected_refusal_observed"] is True
        assert first["malformed_source_unchanged"] is True
        assert second["malformed_source_unchanged"] is True
        assert first["purpose"] == "malformed_control_excluded_from_artifact_count"
        assert identities[0].input_sha256 != identities[1].input_sha256
        assert identities[0].name != identities[1].name
        assert not list(tmp_path.glob("malformed-control-*.3mf"))
        assert (
            summarize(controls, elapsed_seconds=7200)["distinct_usable_artifacts"] == 0
        )


class TestRemainingJobSeconds:
    @pytest.mark.parametrize("now", [10.0, 10.01], ids=["exact-deadline", "expired"])
    def test_rejects_an_exhausted_work_window(self, now):
        from scripts.qualify_ingestion import remaining_job_seconds

        with pytest.raises(
            TimeoutError, match="qualification_workload_deadline_exhausted"
        ):
            remaining_job_seconds(10.0, 180, clock=lambda: now)

    @pytest.mark.parametrize("maximum,expected", [(180.0, 7.0), (3.0, 3.0)])
    def test_caps_the_job_at_remaining_work_time(self, maximum, expected):
        from scripts.qualify_ingestion import remaining_job_seconds

        assert remaining_job_seconds(12.0, maximum, clock=lambda: 5.0) == expected
