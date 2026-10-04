"""A mesh derivative that misbehaves costs the child, never the parent.

`supervise` is the whole safety argument for running geometry work out of the API
process (#259): whatever the child does with a hostile file, the parent must get
its memory back, must not wait forever, and must not leave descendants running.
Each test below is a child that misbehaves in one specific way, and each checks
the process is really gone afterwards — a supervisor that reports the right
failure while the child keeps growing has protected nothing.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from threading import Event, Thread, current_thread

import pytest
from printstash_core.mesh.measurements import (
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
    volume_value,
)

from app.modules.media import mesh_isolation
from app.modules.media.fingerprints import (
    FingerprintRecord,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_contracts import (
    GeometryNotLoaded,
    GeometryNotRequested,
    GeometryReady,
    GeometryRefused,
    MeshCoverage,
    PreviewCoverage,
    SourceScanState,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
)
from app.modules.media.mesh_facts import (
    CompleteGeometry,
    FingerprintFailureCode,
    SampledGeometry,
)
from app.modules.media.mesh_isolation import (
    MeshWorkerError,
    decode_reply,
    encode_reply,
    supervise,
)

MB = 1024 * 1024


def _child(script: str, *args: str) -> list[str]:
    return [sys.executable, "-c", script, *args]


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A killed child that nobody has reaped still answers signal 0.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return False


def _wait_gone(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not _alive(pid):
            return True
        time.sleep(0.05)
    return not _alive(pid)


class TestSupervise:
    def test_returns_what_the_child_wrote(self):
        reply = supervise(
            _child("import sys; sys.stdout.buffer.write(b'framed')"),
            memory_budget=512 * MB,
            timeout_seconds=30,
        )

        assert reply == b"framed"

    def test_kills_a_child_that_outgrows_its_memory_budget(self, tmp_path):
        pid_file = tmp_path / "pid"
        script = (
            "import os, sys, time\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "hold = b'x' * (400 * 1024 * 1024)\n"  # touched, so it is resident
            "time.sleep(60)\n"
        )

        started = time.monotonic()
        with pytest.raises(MeshWorkerError) as raised:
            supervise(
                _child(script, str(pid_file)),
                memory_budget=150 * MB,
                timeout_seconds=60,
            )

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert time.monotonic() - started < 30
        assert _wait_gone(int(pid_file.read_text()))

    def test_kills_a_child_that_outlives_its_deadline(self, tmp_path):
        pid_file = tmp_path / "pid"
        script = (
            "import os, sys, time\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "time.sleep(60)\n"
        )

        with pytest.raises(MeshWorkerError) as raised:
            supervise(
                _child(script, str(pid_file)),
                memory_budget=512 * MB,
                timeout_seconds=1,
            )

        assert raised.value.reason is ThumbnailFailureReason.TIMEOUT
        assert _wait_gone(int(pid_file.read_text()))

    def test_kills_descendants_the_child_started(self, tmp_path):
        """A native loader may fork; the kill has to reach the whole group."""
        grandchild = tmp_path / "grandchild"
        script = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            "open(sys.argv[1], 'w').write(str(child.pid))\n"
            "time.sleep(60)\n"
        )

        with pytest.raises(MeshWorkerError):
            supervise(
                _child(script, str(grandchild)),
                memory_budget=512 * MB,
                timeout_seconds=1.5,
            )

        assert _wait_gone(int(grandchild.read_text()))

    def test_preserves_legacy_refusal_reason_for_sigkill(self):
        with pytest.raises(MeshWorkerError) as raised:
            supervise(
                _child("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"),
                memory_budget=512 * MB,
                timeout_seconds=30,
            )

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert raised.value.supervision.exit_cause.value == "sigkill"

    def test_a_child_that_exits_with_an_error_is_a_worker_failure(self):
        with pytest.raises(MeshWorkerError) as raised:
            supervise(
                _child("import sys; sys.stdout.buffer.write(b'partial'); sys.exit(3)"),
                memory_budget=512 * MB,
                timeout_seconds=30,
            )

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_refuses_a_reply_larger_than_the_frame_limit(self, monkeypatch):
        monkeypatch.setattr(mesh_isolation, "MAX_REPLY_BYTES", 1024)

        with pytest.raises(MeshWorkerError) as raised:
            supervise(
                _child("import sys; sys.stdout.buffer.write(b'x' * 100000)"),
                memory_budget=512 * MB,
                timeout_seconds=30,
            )

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestSuperviseAfterEof:
    def test_enforces_memory_budget_after_stdout_closes(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "pid"
        script = (
            "import os, sys, time\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "os.close(1)\n"
            "time.sleep(0.3)\n"
            "hold = b'x' * (48 * 1024 * 1024)\n"
            "time.sleep(60)\n"
        )

        with pytest.raises(MeshWorkerError) as error:
            supervise(
                _child(script, str(pid_file)), memory_budget=32 * MB, timeout_seconds=4
            )

        assert _wait_gone(int(pid_file.read_text()))
        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert error.value.supervision.exit_cause.value == "memory_limit"
        assert error.value.supervision.peak_tree_rss_bytes > 32 * MB
        assert error.value.supervision.active_phase is None

    def test_observes_cancellation_after_stdout_closes(self, tmp_path: Path) -> None:
        from app.core.cancellation import cancellation_scope

        marker = tmp_path / "cancel"
        pid_file = tmp_path / "pid"
        script = (
            "import os, sys, time\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "os.close(1)\n"
            "time.sleep(0.3)\n"
            "open(sys.argv[2], 'w').write('cancel')\n"
            "time.sleep(60)\n"
        )
        started = time.monotonic()

        with (
            cancellation_scope(marker.exists),
            pytest.raises(mesh_isolation.MeshWorkerCancelled) as error,
        ):
            supervise(
                _child(script, str(pid_file), str(marker)),
                memory_budget=256 * MB,
                timeout_seconds=4,
            )

        assert _wait_gone(int(pid_file.read_text()))
        assert time.monotonic() - started < 2
        assert error.value.supervision.exit_cause.value == "cancelled"
        assert error.value.supervision.elapsed_ns > 0

    def test_retains_reply_when_child_exits_after_stdout_closes(self) -> None:
        reply = supervise(
            _child(
                "import os, time; os.write(1, b'reply'); os.close(1); time.sleep(0.1)"
            ),
            memory_budget=256 * MB,
            timeout_seconds=4,
        )

        assert reply == b"reply"

    def test_classifies_nonzero_exit_after_stdout_closes(self) -> None:
        with pytest.raises(MeshWorkerError) as error:
            supervise(
                _child(
                    "import os, sys, time; os.close(1); time.sleep(0.1); sys.exit(3)"
                ),
                memory_budget=256 * MB,
                timeout_seconds=4,
            )

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED
        assert error.value.supervision.exit_cause.value == "exited_nonzero"

    def test_enforces_deadline_after_stdout_closes(self, tmp_path: Path) -> None:
        pid_file = tmp_path / "pid"
        script = (
            "import os, sys, time\n"
            "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
            "os.close(1)\n"
            "time.sleep(60)\n"
        )

        with pytest.raises(MeshWorkerError) as error:
            supervise(
                _child(script, str(pid_file)),
                memory_budget=256 * MB,
                timeout_seconds=0.5,
            )

        assert _wait_gone(int(pid_file.read_text()))
        assert error.value.reason is ThumbnailFailureReason.TIMEOUT
        assert error.value.supervision.exit_cause.value == "deadline"


def _result(**overrides) -> ThumbnailResult:
    values = dict(
        image=b"\x89PNG-bytes",
        geometry_outcome=GeometryReady(),
        volume=VolumeMeasured(12.25),
        geometry={
            "bbox_x_mm": 1.5,
            "bbox_y_mm": None,
            "bbox_z_mm": 3.0,
            "volume_mm3": 12.25,
            "triangle_count": 12,
        },
        strategy=ThumbnailStrategy.FULL,
        coverage=MeshCoverage(
            SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.COMPLETE
        ),
        failure_reason=None,
        duration_ms=42,
        peak_rss_bytes=123456,
        fingerprint_result=None,
    )
    values.update(overrides)
    return ThumbnailResult(**values)


class TestNativeDimensionEvidence:
    @pytest.mark.parametrize(
        "value", [float("inf"), float("nan"), -1.0, True, "1.0", 10**400]
    )
    def test_rejects_nonphysical_native_dimensions(self, value):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["geometry"]["bbox_x_mm"] = value
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)
        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestNativeVolumeEvidence:
    @pytest.mark.parametrize(
        "cause",
        [
            VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE,
            VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED,
        ],
    )
    def test_rejects_volume_assessment_when_geometry_was_not_requested(self, cause):
        with pytest.raises(ValueError, match="not requested"):
            _result(
                geometry_outcome=GeometryNotRequested(),
                geometry={"volume_mm3": None},
                volume=VolumeNotCalculated(cause),
            )

    def test_rejects_measured_volume_when_geometry_was_not_requested(self):
        with pytest.raises(ValueError, match="not requested"):
            _result(geometry_outcome=GeometryNotRequested())

    @pytest.mark.parametrize(
        "volume",
        [
            {
                "state": "legacy_unassessed",
                "unit": "mm3",
                "method": None,
                "value_mm3": 12.25,
                "cause": None,
            },
            {
                "state": "measured",
                "unit": "mm3",
                "method": "mesh_surface_integral",
                "value_mm3": True,
                "cause": None,
            },
            {
                "state": "unavailable",
                "unit": "mm3",
                "method": "mesh_surface_integral",
                "value_mm3": None,
                "cause": "unknown",
            },
            {
                "state": "measured",
                "unit": "mm3",
                "method": "mesh_surface_integral",
                "value_mm3": 0.5,
                "cause": None,
            },
        ],
        ids=["legacy", "boolean", "unknown-cause", "scalar-mismatch"],
    )
    def test_rejects_invalid_native_volume_wire(self, volume):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["volume"] = volume
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)
        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_geometry_not_requested_with_measured_wire_volume(self):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["geometry_outcome"] = {"state": "not_requested"}
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)
        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_native_reply_without_volume_evidence(self):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        del header["volume"]
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)
        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestNativeVolumeOwnership:
    @pytest.mark.parametrize(
        "volume",
        [
            VolumeMeasured(12.25),
            VolumeUnavailable(VolumeUnavailableCause.NOT_WATERTIGHT),
        ],
    )
    def test_rejects_assessed_volume_from_refused_geometry(self, volume):
        geometry = dict(_result().geometry)
        geometry["volume_mm3"] = volume_value(volume)
        with pytest.raises(ValueError):
            _result(
                geometry=geometry,
                geometry_outcome=GeometryRefused(ThumbnailFailureReason.INVALID_SOURCE),
                volume=volume,
            )

    @pytest.mark.parametrize(
        "cause",
        [
            VolumeNotCalculatedCause.ENRICHMENT_PENDING,
            VolumeNotCalculatedCause.NOT_APPLICABLE,
        ],
    )
    def test_rejects_durable_row_causes_in_native_output(self, cause):
        geometry = dict(_result().geometry)
        geometry["volume_mm3"] = None
        with pytest.raises(ValueError):
            _result(geometry=geometry, volume=VolumeNotCalculated(cause))

    def test_rejects_assessed_volume_from_refused_native_frame(self):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["geometry_outcome"] = {"state": "refused", "reason": "invalid_source"}
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)
        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestReplyFrame:
    def test_round_trips_a_successful_result(self):
        result = _result()

        assert decode_reply(encode_reply(result)) == result

    def test_round_trips_a_failure_without_an_image(self):
        result = _result(
            image=None,
            strategy=ThumbnailStrategy.NONE,
            coverage=MeshCoverage(
                SourceScanState.COMPLETE,
                CompleteGeometry(),
                PreviewCoverage.NOT_PRODUCED,
            ),
            failure_reason=ThumbnailFailureReason.RESOURCE_LIMIT,
        )

        assert decode_reply(encode_reply(result)) == result

    def test_round_trips_an_empty_image_as_present_not_absent(self):
        """An empty byte string and no image are different answers."""
        result = _result(image=b"")

        assert decode_reply(encode_reply(result)).image == b""

    @pytest.mark.parametrize("state", list(FingerprintResultState))
    def test_round_trips_each_fingerprint_state_as_a_wire_string(self, state):
        record = FingerprintRecord(
            component_index=0,
            instance_count=1,
            values={
                "faces": 12,
                "recipe": {
                    "complete_geometry": {
                        FingerprintResultState.READY: True,
                        FingerprintResultState.PARTIAL: False,
                        FingerprintResultState.FAILED: True,
                        FingerprintResultState.UNSUPPORTED: True,
                    }[state]
                },
            },
            instances=(),
        )
        records = {
            FingerprintResultState.READY: (record,),
            FingerprintResultState.PARTIAL: (record,),
            FingerprintResultState.FAILED: (),
            FingerprintResultState.UNSUPPORTED: (),
        }[state]
        cause = {
            FingerprintResultState.READY: None,
            FingerprintResultState.PARTIAL: FingerprintFailureCode.SAMPLED_SOURCE,
            FingerprintResultState.FAILED: FingerprintFailureCode.ANALYSIS_FAILED,
            FingerprintResultState.UNSUPPORTED: FingerprintFailureCode.UNSUPPORTED_GEOMETRY,
        }[state]
        fingerprint = FingerprintResult(
            state=state, records=records, failure_code=cause
        )
        geometry = {
            FingerprintResultState.READY: CompleteGeometry(),
            FingerprintResultState.PARTIAL: SampledGeometry(
                FingerprintFailureCode.SAMPLED_SOURCE
            ),
            FingerprintResultState.FAILED: CompleteGeometry(),
            FingerprintResultState.UNSUPPORTED: CompleteGeometry(),
        }[state]
        coverage = MeshCoverage(
            SourceScanState.COMPLETE, geometry, PreviewCoverage.COMPLETE
        )
        frame = encode_reply(_result(fingerprint_result=fingerprint, coverage=coverage))
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])

        assert header["fingerprint"]["state"] == state.value
        assert type(header["fingerprint"]["state"]) is str
        assert header["fingerprint"]["failure_code"] == (
            None if cause is None else cause.value
        )
        assert decode_reply(frame).fingerprint_result.state is state
        assert decode_reply(frame).fingerprint_result.failure_code is cause

    def test_round_trips_a_fingerprint_with_its_instances(self):
        fingerprint = FingerprintResult(
            state=FingerprintResultState.READY,
            records=(
                FingerprintRecord(0, 1, {"bbox": (1.0, 2.0, 3.0)}, ()),
                FingerprintRecord(
                    component_index=1,
                    instance_count=2,
                    values={
                        "sh_blob": b"\x00\xffblob",
                        "bbox": (1.0, 2.0, 3.0),
                        "unavailable": [("volume", "open_surface")],
                        "volume": None,
                        "faces": 12,
                    },
                    instances=({"transform": [1.0, 0.0]}, {"transform": [2.0, 0.0]}),
                ),
            ),
        )
        result = _result(fingerprint_result=fingerprint)

        assert decode_reply(encode_reply(result)).fingerprint_result == fingerprint

    @pytest.mark.parametrize("state", ["unknown", "pending", "", None, True, 1, [], {}])
    def test_rejects_an_invalid_fingerprint_state(self, state):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["fingerprint"] = {
            "state": state,
            "failure_code": None,
            "algorithm_version": "x",
            "records": [],
        }
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_a_fingerprint_without_its_state(self):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["fingerprint"] = {
            "failure_code": None,
            "algorithm_version": "x",
            "records": [],
        }
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_preserves_the_type_of_every_fingerprint_value(self):
        """A descriptor blob must stay bytes and a tuple must stay a tuple."""
        record = FingerprintRecord(
            component_index=0,
            instance_count=1,
            values={"blob": b"ab", "pair": (1, 2), "items": [1, 2]},
            instances=(),
        )
        result = _result(
            fingerprint_result=FingerprintResult(
                state=FingerprintResultState.READY, records=(record,)
            )
        )

        values = decode_reply(encode_reply(result)).fingerprint_result.records[0].values

        assert values == {"blob": b"ab", "pair": (1, 2), "items": [1, 2]}
        assert type(values["pair"]) is tuple and type(values["items"]) is list

    def test_refuses_to_encode_a_value_it_cannot_decode_safely(self):
        record = FingerprintRecord(
            component_index=0, instance_count=1, values={"bad": object()}, instances=()
        )
        result = _result(
            fingerprint_result=FingerprintResult(
                state=FingerprintResultState.READY, records=(record,)
            )
        )

        with pytest.raises(TypeError):
            encode_reply(result)

    def test_refuses_a_key_that_could_impersonate_a_tag(self):
        record = FingerprintRecord(
            component_index=0,
            instance_count=1,
            values={"$bytes": "AAAA"},
            instances=(),
        )
        result = _result(
            fingerprint_result=FingerprintResult(
                state=FingerprintResultState.READY, records=(record,)
            )
        )

        with pytest.raises(TypeError):
            encode_reply(result)

    def test_rejects_a_reply_carrying_an_unknown_tag_or_bad_base64(self):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["fingerprint"] = {
            "state": "ready",
            "failure_code": None,
            "algorithm_version": "x",
            "records": [
                {
                    "component_index": 0,
                    "instance_count": 1,
                    "values": {"blob": {"$bytes": "not base64!"}},
                    "instances": [],
                }
            ],
        }
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError):
            decode_reply(forged)

    @pytest.mark.parametrize(
        "payload",
        [
            b"",
            b"NOPE\x00\x00\x00\x02{}",
            b"MSH1\x00\x00",
            b"MSH1\x00\x00\x00\x05{bad}",
            b"MSH1\x00\x00\x00\x02{}",
        ],
        ids=["empty", "wrong-magic", "truncated-length", "not-json", "missing-keys"],
    )
    def test_treats_a_malformed_frame_as_a_worker_failure(self, payload):
        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(payload)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_an_image_whose_length_does_not_match_its_header(self):
        frame = encode_reply(_result())

        with pytest.raises(MeshWorkerError):
            decode_reply(frame + b"trailing")

    def test_rejects_a_strategy_the_engine_does_not_have(self):
        frame = encode_reply(_result()).replace(b'"full"', b'"warp"')

        with pytest.raises(MeshWorkerError):
            decode_reply(frame)


class TestGeometryOutcome:
    def test_refusal_survives_a_successful_preview_reply(self):
        from app.modules.media.mesh_contracts import GeometryRefused

        result = _result(
            geometry_outcome=GeometryRefused(ThumbnailFailureReason.RESOURCE_LIMIT),
            volume=VolumeNotCalculated(VolumeNotCalculatedCause.GEOMETRY_UNAVAILABLE),
            geometry={
                "bbox_x_mm": None,
                "bbox_y_mm": None,
                "bbox_z_mm": None,
                "volume_mm3": None,
                "triangle_count": None,
            },
        )
        assert (
            decode_reply(encode_reply(result)).geometry_outcome
            == result.geometry_outcome
        )

    def test_missing_geometry_outcome_is_not_a_successful_reply(self):
        payload = encode_reply(_result())
        import struct

        length = struct.unpack("!I", payload[4:8])[0]
        header = json.loads(payload[8 : 8 + length])
        del header["geometry_outcome"]
        encoded = json.dumps(header).encode()
        invalid = (
            payload[:4]
            + struct.pack("!I", len(encoded))
            + encoded
            + payload[8 + length :]
        )
        with pytest.raises(MeshWorkerError) as error:
            decode_reply(invalid)
        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestSupervisionStats:
    @pytest.mark.parametrize(
        ("script", "budget", "deadline", "cause"),
        [
            pytest.param(
                "import time; time.sleep(60)", 256 * MB, 0.3, "deadline", id="timeout"
            ),
            pytest.param(
                "import sys, time; time.sleep(0.2); sys.exit(3)",
                256 * MB,
                30,
                "exited_nonzero",
                id="nonzero",
            ),
            pytest.param(
                "import time; hold = b'x' * (32 * 1024 * 1024); time.sleep(60)",
                24 * MB,
                30,
                "memory_limit",
                id="memory",
            ),
        ],
    )
    def test_retains_cost_on_native_failure(
        self, script: str, budget: int, deadline: float, cause: str
    ) -> None:
        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.supervise_result(
                _child(script), memory_budget=budget, timeout_seconds=deadline
            )

        stats = error.value.supervision
        assert stats is not None
        assert stats.elapsed_ns > 0
        assert stats.peak_tree_rss_bytes > 0
        assert stats.exit_cause.value == cause
        assert stats.active_phase is None

    def test_success_retains_reply_cost(self) -> None:
        result = mesh_isolation.supervise_result(
            _child("import sys; sys.stdout.buffer.write(b'hello')"),
            memory_budget=256 * MB,
            timeout_seconds=30,
        )

        assert result.payload == b"hello"
        assert result.stats.reply_bytes == 5
        assert result.stats.elapsed_ns > 0
        assert result.stats.exit_cause.value == "exited_zero"

    def test_preserves_unobserved_rss(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            mesh_isolation.mesh_policy, "process_tree_rss_bytes", lambda _pid: None
        )

        result = mesh_isolation.supervise_result(
            _child("pass"), memory_budget=256 * MB, timeout_seconds=30
        )

        assert result.stats.peak_tree_rss_bytes is None

    def test_retains_partial_reply_on_error(self) -> None:
        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.supervise_result(
                _child("import sys; sys.stdout.buffer.write(b'partial'); sys.exit(3)"),
                memory_budget=256 * MB,
                timeout_seconds=30,
            )

        assert error.value.supervision.reply_bytes == 7

    def test_records_spawn_failure(self) -> None:
        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.supervise_result(
                ["/nonexistent-printstash-worker"],
                memory_budget=256 * MB,
                timeout_seconds=30,
            )

        assert error.value.supervision.exit_cause.value == "spawn_failed"
        assert error.value.supervision.elapsed_ns > 0
        assert error.value.supervision.peak_tree_rss_bytes is None

    def test_read_failure_retains_parent_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        real_read = os.read

        def fail_reply_read(fd: int, size: int) -> bytes:
            if size == 65536:
                raise OSError("injected reply pipe failure")
            return real_read(fd, size)

        monkeypatch.setattr(mesh_isolation.os, "read", fail_reply_read)
        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.supervise_result(
                _child("print('reply')"), memory_budget=256 * MB, timeout_seconds=30
            )

        assert isinstance(error.value.__cause__, OSError)
        assert error.value.supervision.exit_cause.value == "supervision_failed"
        assert error.value.supervision.elapsed_ns > 0

    def test_cleanup_failure_retains_parent_cost(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail_reap(_pid: int) -> None:
            raise OSError("injected reaper failure")

        monkeypatch.setattr(mesh_isolation, "reap_descendants", fail_reap)
        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.supervise_result(
                _child("print('reply')"), memory_budget=256 * MB, timeout_seconds=30
            )

        assert isinstance(error.value.__cause__, OSError)
        assert error.value.supervision.exit_cause.value == "supervision_failed"
        assert error.value.supervision.reply_bytes == 6

    def test_invalid_final_reply_retains_parent_cost(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setattr(
            mesh_isolation,
            "worker_command",
            lambda *_args, **_kwargs: _child("print('invalid')"),
        )
        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.generate(
                ThumbnailRequest(path=tmp_path / "unused.stl", file_type="stl")
            )

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED
        assert error.value.supervision.exit_cause.value == "exited_zero"
        assert error.value.supervision.reply_bytes == 8

    def test_cancellation_retains_parent_cost(self) -> None:
        from app.core.cancellation import cancellation_scope

        with (
            cancellation_scope(lambda: True),
            pytest.raises(mesh_isolation.MeshWorkerCancelled) as error,
        ):
            mesh_isolation.supervise_result(
                _child("import time; time.sleep(60)"),
                memory_budget=256 * MB,
                timeout_seconds=30,
            )

        assert error.value.supervision.exit_cause.value == "cancelled"
        assert error.value.supervision.elapsed_ns > 0
        assert error.value.supervision.active_phase is None

    def test_exports_failure_cost_in_parent(self) -> None:
        from app.core.metrics import registry

        labels = {"cause": "exited_nonzero"}
        before = (
            registry.get_sample_value(
                "printstash_mesh_worker_duration_seconds_sum", labels
            )
            or 0
        )

        with pytest.raises(MeshWorkerError):
            mesh_isolation.supervise_result(
                _child("import sys; sys.exit(2)"),
                memory_budget=256 * MB,
                timeout_seconds=30,
            )

        assert (
            registry.get_sample_value(
                "printstash_mesh_worker_duration_seconds_sum", labels
            )
            > before
        )


class TestPhaseReply:
    def test_requires_versioned_phase_stats(self) -> None:
        frame = encode_reply(_result())
        length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + length])
        del header["phase_stats"]
        body = json.dumps(header).encode()
        legacy = frame[:4] + len(body).to_bytes(4, "big") + body + frame[8 + length :]

        with pytest.raises(MeshWorkerError) as error:
            decode_reply(legacy)

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestCoverageReply:
    def test_complete_scan_survives_partial_preview_transport(self):
        coverage = MeshCoverage(
            SourceScanState.COMPLETE, CompleteGeometry(), PreviewCoverage.PARTIAL
        )
        result = _result(coverage=coverage)

        decoded = decode_reply(encode_reply(result))

        assert decoded.coverage == coverage
        assert decoded.image == result.image

    def test_sampled_geometry_survives_complete_scan_transport(self):
        coverage = MeshCoverage(
            SourceScanState.COMPLETE,
            SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            PreviewCoverage.NOT_PRODUCED,
        )
        result = _result(image=None, strategy=ThumbnailStrategy.NONE, coverage=coverage)

        assert decode_reply(encode_reply(result)).coverage == coverage

    @pytest.mark.parametrize(
        "damage",
        [
            "missing",
            "unknown-source",
            "unknown-preview",
            "unknown-geometry",
            "unknown-cause",
        ],
        ids=str,
    )
    def test_rejects_invalid_native_coverage(self, damage):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        replacements = {
            "missing": None,
            "unknown-source": {
                "source_scan": "unknown",
                "geometry": {"state": "complete"},
                "preview": "complete",
            },
            "unknown-preview": {
                "source_scan": "complete",
                "geometry": {"state": "complete"},
                "preview": "unknown",
            },
            "unknown-geometry": {
                "source_scan": "complete",
                "geometry": {"state": "unknown"},
                "preview": "complete",
            },
            "unknown-cause": {
                "source_scan": "partial",
                "geometry": {"state": "sampled", "reason": "unknown"},
                "preview": "partial",
            },
        }
        header["coverage"] = replacements[damage]
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_missing_native_coverage(self):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        del header["coverage"]
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    @pytest.mark.parametrize(
        "cause",
        ["invented_failure", "", True, 1],
        ids=["unknown", "empty", "boolean", "number"],
    )
    def test_rejects_unknown_fingerprint_cause(self, cause):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["fingerprint"] = {
            "state": "failed",
            "failure_code": cause,
            "algorithm_version": "x",
            "records": [],
        }
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestRaiseReportedError:
    def test_preserves_known_worker_cause(self):
        from printstash_core.mesh.similarity import GeometryError

        with pytest.raises(GeometryError) as raised:
            mesh_isolation.raise_reported_error(b"ERR1resource_limit")

        assert raised.value.code == "resource_limit"

    @pytest.mark.parametrize(
        "payload",
        [b"ERR1invented_failure", b"ERR1", b"ERR1invalid\xff"],
        ids=["unknown", "empty", "non-ascii"],
    )
    def test_rejects_unrecognized_worker_cause(self, payload):
        with pytest.raises(MeshWorkerError) as raised:
            mesh_isolation.raise_reported_error(payload)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


class TestNativePreviewOwnership:
    @pytest.mark.parametrize(
        ("image", "preview"),
        [(None, PreviewCoverage.COMPLETE), (b"preview", PreviewCoverage.NOT_PRODUCED)],
        ids=["missing-image", "unreported-image"],
    )
    def test_rejects_preview_fact_inconsistent_with_image(self, image, preview):
        coverage = MeshCoverage(SourceScanState.COMPLETE, CompleteGeometry(), preview)

        with pytest.raises(
            ValueError, match="preview coverage disagrees with image presence"
        ):
            _result(image=image, coverage=coverage)

    @pytest.mark.parametrize(
        ("strategy", "preview"),
        [
            (ThumbnailStrategy.EMBEDDED, PreviewCoverage.COMPLETE),
            (ThumbnailStrategy.FULL, PreviewCoverage.DOCUMENT_SUPPLIED),
        ],
        ids=["embedded-without-provenance", "rendered-with-document-provenance"],
    )
    def test_rejects_document_provenance_inconsistent_with_strategy(
        self, strategy, preview
    ):
        coverage = MeshCoverage(SourceScanState.COMPLETE, CompleteGeometry(), preview)

        with pytest.raises(ValueError, match="requires"):
            _result(strategy=strategy, coverage=coverage)

    def test_rejects_untyped_coverage(self):
        with pytest.raises(TypeError, match="invalid mesh coverage"):
            _result(coverage="complete")


class TestEncodeError:
    def test_retains_known_native_failure_literal(self):
        from printstash_core.mesh.similarity import GeometryError

        assert (
            mesh_isolation.encode_error(GeometryError("resource_limit"))
            == b"ERR1resource_limit"
        )

    def test_rejects_unrecognized_native_failure_literal(self):
        from printstash_core.mesh.similarity import GeometryError

        with pytest.raises(ValueError, match="is not a valid FingerprintFailureCode"):
            mesh_isolation.encode_error(GeometryError("invented_failure"))


class TestNativeFingerprintOwnership:
    @pytest.mark.parametrize(
        ("state", "geometry"),
        [
            (FingerprintResultState.READY, GeometryNotLoaded()),
            (
                FingerprintResultState.READY,
                SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            ),
            (FingerprintResultState.PARTIAL, CompleteGeometry()),
            (FingerprintResultState.PARTIAL, GeometryNotLoaded()),
        ],
        ids=["ready-unloaded", "ready-sampled", "partial-full", "partial-unloaded"],
    )
    def test_rejects_successful_analysis_with_wrong_representation(
        self, state, geometry
    ):
        record = FingerprintRecord(
            0,
            1,
            {
                "recipe": {
                    "complete_geometry": {
                        FingerprintResultState.READY: True,
                        FingerprintResultState.PARTIAL: False,
                    }[state]
                }
            },
            (),
        )
        cause = {
            FingerprintResultState.READY: None,
            FingerprintResultState.PARTIAL: FingerprintFailureCode.SAMPLED_SOURCE,
        }[state]
        fingerprint = FingerprintResult(state, (record,), cause)
        coverage = MeshCoverage(
            SourceScanState.COMPLETE, geometry, PreviewCoverage.COMPLETE
        )

        with pytest.raises(ValueError, match="fingerprint requires"):
            _result(fingerprint_result=fingerprint, coverage=coverage)

    def test_rejects_partial_cause_disagreeing_with_sample(self):
        fingerprint = FingerprintResult(
            FingerprintResultState.PARTIAL,
            (FingerprintRecord(0, 1, {"recipe": {"complete_geometry": False}}, ()),),
            FingerprintFailureCode.SAMPLED_OVERSIZED_SOURCE,
        )
        coverage = MeshCoverage(
            SourceScanState.COMPLETE,
            SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            PreviewCoverage.COMPLETE,
        )

        with pytest.raises(
            ValueError,
            match="partial fingerprint cause disagrees with sampled geometry",
        ):
            _result(fingerprint_result=fingerprint, coverage=coverage)

    @pytest.mark.parametrize(
        ("state", "geometry"),
        [
            ("ready", {"state": "not_loaded"}),
            ("ready", {"state": "sampled", "reason": "sampled_source"}),
            ("partial", {"state": "complete"}),
            ("partial", {"state": "not_loaded"}),
        ],
        ids=["ready-unloaded", "ready-sampled", "partial-full", "partial-unloaded"],
    )
    def test_rejects_forged_successful_analysis_representation(self, state, geometry):
        frame = encode_reply(_result())
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["coverage"]["geometry"] = geometry
        header["fingerprint"] = {
            "state": state,
            "failure_code": {"ready": None, "partial": "sampled_source"}[state],
            "algorithm_version": "x",
            "records": [
                {
                    "component_index": 0,
                    "instance_count": 1,
                    "values": {
                        "recipe": {
                            "complete_geometry": {"ready": True, "partial": False}[
                                state
                            ]
                        }
                    },
                    "instances": [],
                }
            ],
        }
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED

    def test_rejects_forged_partial_cause_disagreeing_with_sample(self):
        fingerprint = FingerprintResult(
            FingerprintResultState.PARTIAL,
            (FingerprintRecord(0, 1, {"recipe": {"complete_geometry": False}}, ()),),
            FingerprintFailureCode.SAMPLED_SOURCE,
        )
        coverage = MeshCoverage(
            SourceScanState.COMPLETE,
            SampledGeometry(FingerprintFailureCode.SAMPLED_SOURCE),
            PreviewCoverage.COMPLETE,
        )
        frame = encode_reply(_result(fingerprint_result=fingerprint, coverage=coverage))
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])
        header["fingerprint"]["failure_code"] = "sampled_oversized_source"
        body = json.dumps(header).encode()
        forged = (
            b"MSH1" + len(body).to_bytes(4, "big") + body + frame[8 + header_length :]
        )

        with pytest.raises(MeshWorkerError) as raised:
            decode_reply(forged)

        assert raised.value.reason is ThumbnailFailureReason.WORKER_FAILED


@pytest.fixture
def staged_native(tmp_path, monkeypatch):
    from app.core.config import _overlay
    from app.modules.media.mesh_protocol import (
        FingerprintFinal,
        GeometryOutput,
        encode_frame,
    )

    request = ThumbnailRequest(tmp_path / "source.stl", include_thumbnail=False)
    request.path.write_bytes(b"source")
    coverage = MeshCoverage(
        SourceScanState.COMPLETE, GeometryNotLoaded(), PreviewCoverage.NOT_PRODUCED
    )
    geometry = GeometryOutput(
        {
            "bbox_x_mm": 1.0,
            "bbox_y_mm": 2.0,
            "bbox_z_mm": 3.0,
            "triangle_count": 12,
            "volume_mm3": 6.0,
        },
        GeometryReady(),
        VolumeMeasured(6.0),
        coverage,
        1,
        None,
    )
    basic = tmp_path / "basic"
    basic.write_bytes(encode_frame(geometry, sequence=0))
    complete = tmp_path / "complete"
    complete.write_bytes(
        basic.read_bytes()
        + encode_frame(FingerprintFinal(None, (), 2, None, coverage), sequence=1)
    )
    pids = tmp_path / "pids"

    def configure(mode, *, final=False, budget=256 * MB):
        monkeypatch.setitem(_overlay, "mesh_worker_timeout_seconds", 1.0)
        monkeypatch.setattr(mesh_isolation, "memory_budget_bytes", lambda: budget)
        monkeypatch.setattr(
            mesh_isolation,
            "worker_command",
            lambda *_args, **_kwargs: [
                sys.executable,
                "-m",
                "tests.fakes.mesh_staged_output_probe",
                mode,
                str(complete if final else basic),
                str(pids),
            ],
        )

    return request, geometry, pids, configure


class TestStagedNativeOutputs:
    def test_preserves_basic_output_before_deadline(self, staged_native):
        request, geometry, pids, configure = staged_native
        configure("stall")
        outputs = []
        observed_live_child = []

        def publish(output):
            outputs.append(output)
            observed_live_child.append(_alive(json.loads(pids.read_text())["parent"]))

        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.generate(request, on_output=publish)

        assert error.value.reason is ThumbnailFailureReason.TIMEOUT
        assert outputs == [geometry]
        assert observed_live_child == [True]
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())

    def test_rejects_final_before_nonzero_exit(self, staged_native):
        request, geometry, pids, configure = staged_native
        configure("error", final=True)
        outputs = []

        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.generate(request, on_output=outputs.append)

        assert error.value.reason is ThumbnailFailureReason.WORKER_FAILED
        assert outputs == [geometry]
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())

    def test_keeps_deadline_active_after_final_eof(self, staged_native):
        request, geometry, pids, configure = staged_native
        configure("final_stall", final=True)
        outputs = []

        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.generate(request, on_output=outputs.append)

        assert error.value.reason is ThumbnailFailureReason.TIMEOUT
        assert outputs == [geometry]
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())

    def test_keeps_rss_limit_active_after_final_eof(self, staged_native):
        request, geometry, pids, configure = staged_native
        configure("grow", final=True, budget=64 * MB)
        outputs = []

        with pytest.raises(MeshWorkerError) as error:
            mesh_isolation.generate(request, on_output=outputs.append)

        assert error.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT
        assert outputs == [geometry]
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())

    def test_callback_failure_reaps_the_worker_tree(self, staged_native):
        request, _geometry, pids, configure = staged_native
        configure("stall")
        fault = RuntimeError("publication refused")

        def publish(output):
            raise fault

        with pytest.raises(RuntimeError) as error:
            mesh_isolation.generate(request, on_output=publish)

        assert error.value is fault
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())

    def test_cancellation_preserves_basic_output(self, staged_native):
        from app.core.cancellation import cancellation_scope

        request, geometry, pids, configure = staged_native
        configure("stall")
        outputs = []

        with cancellation_scope(lambda: bool(outputs)):
            with pytest.raises(mesh_isolation.MeshWorkerCancelled):
                mesh_isolation.generate(request, on_output=outputs.append)

        assert outputs == [geometry]
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())

    def test_adopts_final_only_after_successful_exit(self, staged_native):
        request, geometry, pids, configure = staged_native
        configure("done", final=True)
        outputs = []

        result = mesh_isolation.generate(request, on_output=outputs.append)

        assert outputs == [geometry]
        assert result.geometry == geometry.geometry
        assert result.volume == geometry.volume
        assert result.image is None
        assert result.supervision.exit_cause.value == "exited_zero"
        assert all(_wait_gone(pid) for pid in json.loads(pids.read_text()).values())


@pytest.fixture
def blocked_output_callback(staged_native, mode, budget):
    """A callback remains blocked until the test has observed native termination."""
    request, geometry, pids, configure = staged_native
    configure(mode, budget=budget)
    entered = Event()
    release = Event()
    outputs = []
    errors = []

    def publish(output):
        outputs.append(output)
        entered.set()
        release.wait(10)

    def supervise():
        try:
            mesh_isolation.generate(request, on_output=publish)
        except BaseException as exc:
            errors.append(exc)

    worker = Thread(target=supervise, name="test-blocked-mesh-publication")
    worker.start()
    try:
        yield entered, release, worker, outputs, errors, geometry, pids
    finally:
        release.set()
        worker.join(5)


class TestBlockedOutputCallback:
    @pytest.mark.parametrize(
        "mode,budget,reason,cause",
        [
            pytest.param(
                "stall",
                256 * MB,
                ThumbnailFailureReason.TIMEOUT,
                "deadline",
                id="deadline",
            ),
            pytest.param(
                "grow",
                64 * MB,
                ThumbnailFailureReason.RESOURCE_LIMIT,
                "memory_limit",
                id="tree-rss",
            ),
        ],
    )
    def test_terminates_native_work_before_callback_returns(
        self, blocked_output_callback, reason, cause
    ):
        entered, release, worker, outputs, errors, geometry, pids = (
            blocked_output_callback
        )
        assert entered.wait(3), "worker must emit a real validated output"
        recorded = json.loads(pids.read_text())
        try:
            terminated_while_blocked = all(
                _wait_gone(pid, seconds=2) for pid in recorded.values()
            )
            callback_still_blocked = worker.is_alive()
        finally:
            release.set()
            worker.join(5)

        assert terminated_while_blocked, (
            "native limits must run while publication is blocked"
        )
        assert callback_still_blocked
        assert not worker.is_alive()
        assert outputs == [geometry]
        assert len(errors) == 1
        assert isinstance(errors[0], MeshWorkerError)
        assert errors[0].reason is reason
        assert errors[0].supervision.exit_cause.value == cause
        assert all(_wait_gone(pid) for pid in recorded.values())


@pytest.fixture
def failed_watchdog_launch(monkeypatch, launch_mode):
    """Keep real thread startup, native cleanup, and delayed monitor observations."""
    fault = {
        "post-start": KeyboardInterrupt("interrupted thread startup"),
        "never-start": RuntimeError("cannot start new thread"),
    }[launch_mode]
    original_watchdog = mesh_isolation._CallbackWatchdog
    original_kill = os.killpg
    monitors = []
    cleanup_stopped = []
    late_signals = []
    cleanup_started = Event()
    owner_thread = current_thread()

    class InterruptedThread(Thread):
        def start(self):
            if launch_mode == "post-start":
                super().start()
            raise fault

    def observe_watchdog(*args, **kwargs):
        monitor = original_watchdog(*args, **kwargs)
        monitors.append(monitor)
        return monitor

    def kill_group(pid, sig):
        if current_thread() is owner_thread:
            cleanup_stopped.append(monitors[0].stopped.is_set())
            cleanup_started.set()
        elif cleanup_started.is_set():
            # Observe a forbidden late action without signalling a reused group.
            late_signals.append((pid, sig))
            return
        return original_kill(pid, sig)

    monkeypatch.setattr(mesh_isolation, "Thread", InterruptedThread)
    monkeypatch.setattr(mesh_isolation, "_CallbackWatchdog", observe_watchdog)
    monkeypatch.setattr(os, "killpg", kill_group)
    try:
        yield fault, monitors, cleanup_stopped, late_signals
    finally:
        for monitor in monitors:
            monitor.stopped.set()
            if monitor.thread.is_alive():
                monitor.thread.join(1)


class TestWatchdogStartup:
    @pytest.mark.parametrize(
        "launch_mode",
        ["post-start", "never-start"],
        ids=["interrupted-after-launch", "never-launched"],
    )
    def test_withdraws_monitor_before_failed_launch_cleanup(
        self, failed_watchdog_launch
    ):
        fault, monitors, cleanup_stopped, late_signals = failed_watchdog_launch
        with pytest.raises(type(fault)) as error:
            mesh_isolation.supervise_result(
                _child("import time; time.sleep(60)"),
                memory_budget=256 * MB,
                timeout_seconds=0.15,
                on_chunk=lambda _chunk: None,
            )
        # Wait beyond the native deadline without invoking the monitor's owner.
        # The never-launched variant has no active thread; Event.wait is uniform.
        finished = Event()
        finished.wait(0.3)

        assert error.value is fault
        assert cleanup_stopped
        assert all(cleanup_stopped), "monitor must be withdrawn before PID cleanup"
        assert late_signals == []
        assert monitors[0].stopped.is_set()
        assert not monitors[0].thread.is_alive()
        assert _wait_gone(monitors[0].pid)
