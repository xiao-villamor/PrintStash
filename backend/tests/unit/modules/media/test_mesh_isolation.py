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
from app.modules.media.fingerprints import FingerprintRecord, FingerprintResult
from app.modules.media.mesh_isolation import (
    MeshWorkerError,
    decode_reply,
    encode_reply,
    supervise,
)
from app.modules.media.thumbnail_engine import (
    ThumbnailFailureReason,
    ThumbnailResult,
    ThumbnailStrategy,
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

    def test_reads_a_sigkill_as_the_kernel_running_out_of_memory(self):
        with pytest.raises(MeshWorkerError) as raised:
            supervise(
                _child("import os, signal; os.kill(os.getpid(), signal.SIGKILL)"),
                memory_budget=512 * MB,
                timeout_seconds=30,
            )

        assert raised.value.reason is ThumbnailFailureReason.RESOURCE_LIMIT

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


def _result(**overrides) -> ThumbnailResult:
    values = dict(
        image=b"\x89PNG-bytes",
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

    def test_round_trips_a_fingerprint_with_its_instances(self):
        fingerprint = FingerprintResult(
            state="ready",
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

    def test_keeps_bytes_and_tuples_distinct_from_lists(self):
        """A descriptor blob must stay bytes and a tuple must stay a tuple."""
        record = FingerprintRecord(
            component_index=0,
            instance_count=1,
            values={"blob": b"ab", "pair": (1, 2), "items": [1, 2]},
            instances=(),
        )
        result = _result(
            fingerprint_result=FingerprintResult(state="ready", records=(record,))
        )

        values = decode_reply(encode_reply(result)).fingerprint_result.records[0].values

        assert values == {"blob": b"ab", "pair": (1, 2), "items": [1, 2]}
        assert type(values["pair"]) is tuple and type(values["items"]) is list

    def test_refuses_to_encode_a_value_it_cannot_decode_safely(self):
        record = FingerprintRecord(
            component_index=0, instance_count=1, values={"bad": object()}, instances=()
        )
        result = _result(
            fingerprint_result=FingerprintResult(state="ready", records=(record,))
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
            fingerprint_result=FingerprintResult(state="ready", records=(record,))
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
