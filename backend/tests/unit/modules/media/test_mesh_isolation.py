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

import pytest

from app.modules.media import mesh_isolation
from app.modules.media.fingerprints import (
    FingerprintRecord,
    FingerprintResult,
    FingerprintResultState,
)
from app.modules.media.mesh_contracts import (
    GeometryReady,
    ThumbnailFailureReason,
    ThumbnailRequest,
    ThumbnailResult,
    ThumbnailStrategy,
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
        geometry={
            "bbox_x_mm": 1.5,
            "bbox_y_mm": None,
            "bbox_z_mm": 3.0,
            "volume_mm3": 12.25,
            "triangle_count": 12,
        },
        strategy=ThumbnailStrategy.FULL,
        complete=True,
        failure_reason=None,
        duration_ms=42,
        peak_rss_bytes=123456,
        fingerprint_result=None,
    )
    values.update(overrides)
    return ThumbnailResult(**values)


class TestReplyFrame:
    def test_round_trips_a_successful_result(self):
        result = _result()

        assert decode_reply(encode_reply(result)) == result

    def test_round_trips_a_failure_without_an_image(self):
        result = _result(
            image=None,
            strategy=ThumbnailStrategy.NONE,
            complete=False,
            failure_reason=ThumbnailFailureReason.RESOURCE_LIMIT,
        )

        assert decode_reply(encode_reply(result)) == result

    def test_round_trips_an_empty_image_as_present_not_absent(self):
        """An empty byte string and no image are different answers."""
        result = _result(image=b"")

        assert decode_reply(encode_reply(result)).image == b""

    @pytest.mark.parametrize("state", list(FingerprintResultState))
    def test_round_trips_each_fingerprint_state_as_a_wire_string(self, state):
        fingerprint = FingerprintResult(state=state)
        frame = encode_reply(_result(fingerprint_result=fingerprint))
        header_length = int.from_bytes(frame[4:8], "big")
        header = json.loads(frame[8 : 8 + header_length])

        assert header["fingerprint"]["state"] == state.value
        assert type(header["fingerprint"]["state"]) is str
        assert decode_reply(frame).fingerprint_result.state is state

    def test_round_trips_a_fingerprint_with_its_instances(self):
        fingerprint = FingerprintResult(
            state=FingerprintResultState.READY,
            records=(
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
            geometry_outcome=GeometryRefused(ThumbnailFailureReason.RESOURCE_LIMIT)
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
            mesh_isolation.native_process, "process_tree_rss_bytes", lambda _pid: None
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
