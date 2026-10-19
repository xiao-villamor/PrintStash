"""Real IPC fault isolation; the native executor is deliberately injected."""

import fcntl
import json
import os
import socket
import subprocess
import sys

import pytest

from tests.fixtures.compute_renderer import qualified_renderer as qualified_renderer
from tests.paths import BACKEND_DIR


@pytest.fixture
def render_probe(tmp_path):
    def run(scenario):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_render_lifecycle_probe",
                str(tmp_path),
                scenario,
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    return run


class TestBroker:
    def test_owns_a_separate_process_budget_for_controlled_rendering(
        self, qualified_renderer
    ):
        assert qualified_renderer.owner_pid is not None
        assert qualified_renderer.owner_pid > 0
        assert qualified_renderer.owner_pid != os.getpid()

    def test_isolates_bad_members_of_a_real_socket_batch(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_scheduler_probe",
                str(tmp_path),
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )

        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["batches"][0] > 1
        assert max(report["batches"]) <= 8
        assert report["results"][0]["vectors"] == [[0.0]]
        assert report["results"][1]["vectors"] == [[1.0]]
        assert report["results"][2]["code"] == "embedding_input_invalid"
        assert report["results"][3]["vectors"] == [[3.0]]
        assert report["singleton"]["vectors"] == [[4.0]]
        assert report["elapsed"] < 0.5

    def test_preserves_render_members_after_batch_refusal(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_render_scheduler_probe",
                str(tmp_path),
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["results"] == [
            ["0", "1", "2", "3"],
            ["4", {"error": "invalid_input"}, "5", "6"],
            ["7", "capacity", "8", "9"],
        ]
        assert max(report["batches"]) > 1
        assert max(report["batches"]) <= 8
        assert report["singleton"] == "single"
        assert report["singleton_seconds"] < 0.5

    def test_refuses_staging_pressure_without_poisoning_owner(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_render_scheduler_probe",
                str(tmp_path),
                "staging",
            ],
            cwd=BACKEND_DIR,
            env={**os.environ, "VAULT_COMPUTE_MODE": "cpu"},
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report == {"refusal": "capacity", "healthy": "healthy", "cooldown": 0.0}


@pytest.fixture(params=[False, True], ids=["normal-fd", "high-fd"])
def live_peer(request):
    left, right = socket.socketpair()
    try:
        if request.param:
            descriptor = fcntl.fcntl(left.fileno(), fcntl.F_DUPFD_CLOEXEC, 2048)
            left.close()
            left = socket.socket(fileno=descriptor)
            assert left.fileno() >= 2048
        left.settimeout(1)
        yield left, right
    finally:
        left.close()
        right.close()


class TestPeerLiveness:
    def test_checks_idle_peer_without_timeout_delay(self, live_peer):
        import time

        from app.runtime.compute.protocol import peer_disconnected

        left, _ = live_peer
        start = time.monotonic()
        assert peer_disconnected(left) is False
        assert time.monotonic() - start < 0.25

    def test_detects_closed_peer(self, live_peer):
        from app.runtime.compute.protocol import peer_disconnected

        left, right = live_peer
        right.close()
        assert peer_disconnected(left) is True

    def test_preserves_pending_peer_bytes(self, live_peer):
        from app.runtime.compute.protocol import peer_disconnected

        left, right = live_peer
        right.sendall(b"x")
        assert peer_disconnected(left) is False
        assert left.recv(1) == b"x"


class TestControlExchange:
    def test_reads_a_ready_reply(self, live_peer, tmp_path, monkeypatch):
        import time

        from app.runtime.compute import client
        from app.runtime.compute.protocol import StatusRequest, receive, send

        left, right = live_peer
        monkeypatch.setattr(client, "directory", lambda: tmp_path)
        monkeypatch.setattr(client, "_connect", lambda *args, **kwargs: left)
        send(right, b'{"ready":true}')
        send(right, b'{"healthy":true}')
        deadline = time.monotonic() + 2

        assert (
            client.exchange(StatusRequest(), deadline=deadline) == b'{"healthy":true}'
        )
        assert json.loads(receive(right, deadline)) == {"operation": "status"}
        assert left.fileno() == -1

    def test_withdraws_while_waiting_for_reply(self, live_peer, tmp_path, monkeypatch):
        from app.core.cancellation import OperationCancelled
        from app.runtime.compute import client
        from app.runtime.compute.protocol import StatusRequest, send

        left, right = live_peer
        monkeypatch.setattr(client, "directory", lambda: tmp_path)
        monkeypatch.setattr(client, "_connect", lambda *args, **kwargs: left)
        send(right, b'{"ready":true}')

        def checkpoint():
            try:
                queued = right.recv(4096, socket.MSG_PEEK | socket.MSG_DONTWAIT)
            except BlockingIOError:
                return
            if len(queued) > 4:
                raise OperationCancelled()

        with pytest.raises(OperationCancelled):
            client.exchange(StatusRequest(), checkpoint=checkpoint)
        assert left.fileno() == -1

    def test_preserves_reply_deadline(self, live_peer, tmp_path, monkeypatch):
        import time

        from app.runtime.compute import client
        from app.runtime.compute.contracts import ComputeUnavailable, Reason
        from app.runtime.compute.protocol import StatusRequest, send

        left, right = live_peer
        monkeypatch.setattr(client, "directory", lambda: tmp_path)
        monkeypatch.setattr(client, "_connect", lambda *args, **kwargs: left)
        send(right, b'{"ready":true}')
        deadline = time.monotonic() + 0.1

        with pytest.raises(ComputeUnavailable) as exc:
            client.exchange(StatusRequest(), deadline=deadline)
        assert exc.value.reason is Reason.DEADLINE
        assert left.fileno() == -1


class TestRenderLifecycle:
    def test_packs_multiview_requests_within_eight_frames(self, render_probe):
        report = render_probe("frames")

        assert report["results"] == ["0", "1", "2"]
        assert sorted(map(len, report["batches"][1:-1])) == [1, 2]
        assert report["healthy"] == "healthy"

    def test_preserves_live_members_after_a_deadline(self, render_probe):
        report = render_probe("deadline")

        assert report["results"] == ["deadline", "live"]
        assert sorted(report["batches"][1]) == ["live", "short"]
        assert report["healthy"] == "healthy"

    def test_prioritizes_interactive_ready_work(self, render_probe):
        report = render_probe("interactive")

        assert report["batches"] == [
            ["blocker"],
            ["interactive"],
            ["background"],
            ["healthy"],
        ]
        assert report["results"] == ["background", "interactive"]

    def test_preserves_live_members_after_cancellation(self, render_probe):
        report = render_probe("cancel")

        assert report["results"] == ["cancelled", "live"]
        assert sorted(report["batches"][1]) == ["cancelled", "live"]
        assert report["healthy"] == "healthy"

    def test_reports_admitted_render_priorities(self, render_probe):
        report = render_probe("interactive")

        assert report["interactive"] == 2
        assert report["background"] == 2


class TestNativeOwnerFailures:
    @pytest.mark.parametrize("scenario", ["memory", "plugin"])
    def test_retires_owner_after_native_executor_fault(self, tmp_path, scenario):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_fault_probe",
                str(tmp_path),
                scenario,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["responses"] == [{"error": "device_failed"}]
        assert report["cooled"] is True
        assert not (tmp_path / "runtime/compute/broker.sock").exists()

    @pytest.mark.parametrize("scenario", ["rss", "deadline"])
    def test_watchdog_reaps_unsafe_native_owner(self, tmp_path, scenario):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_fault_probe",
                str(tmp_path),
                scenario,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            timeout=20,
        )
        assert result.returncode == 74, result.stderr
        assert float((tmp_path / "runtime/compute/cooldown").read_text()) > 0

    def test_preempts_background_inference_for_interactive_work(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_fault_probe",
                str(tmp_path),
                "preempt",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["order"] == ["interactive", "background"]
        assert report["responses"] == [{"result": "cmVzdWx0"}] * 2
        assert report["cooled"] is False


class TestInferenceBatchResults:
    @pytest.mark.parametrize("scenario", ["healthy", "invalid"])
    def test_retains_valid_results_across_batch_validation(self, tmp_path, scenario):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_scheduler_probe",
                str(tmp_path),
                scenario,
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert max(report["batches"]) > 1
        assert max(report["batches"]) <= 8
        assert report["results"][0]["vectors"] == [[0.0]]
        assert report["results"][2]["vectors"] == [[2.0]]
        assert report["results"][3]["vectors"] == [[3.0]]
        if scenario == "healthy":
            assert report["results"][1]["vectors"] == [[1.0]]
        else:
            assert report["results"][1] == {"error": "invalid_input"}
        assert report["singleton"]["vectors"] == [[4.0]]


class TestWireAdmissionRefusals:
    def test_refuses_expired_request_on_the_receiver(self, qualified_renderer):
        import time

        from app.runtime.compute import client
        from app.runtime.compute.protocol import (
            InferenceRequest,
            Priority,
            receive,
            send_admitted,
        )

        request = InferenceRequest(
            deadline=1,
            priority=Priority.BACKGROUND,
            request_id="expired",
            directory="/models",
            model_key="test",
            threads=1,
            payload="",
        )
        deadline = time.monotonic() + 2
        with client._connect(client.directory(), deadline, start=False) as connection:
            send_admitted(connection, request.model_dump_json().encode(), deadline)
            assert json.loads(receive(connection, deadline)) == {"error": "deadline"}

    @pytest.mark.parametrize("fault", ["declaration", "upload", "digest"])
    def test_refuses_geometry_length_mismatch(self, qualified_renderer, fault):
        import time

        import numpy as np
        from printstash_core.mesh.render_geometry import PreparedRender

        from app.modules.media.compute_geometry import encode, split
        from app.runtime.compute import client
        from app.runtime.compute.protocol import (
            BinaryRenderRequest,
            Priority,
            receive,
            send,
            send_admitted,
        )

        prepared = PreparedRender(
            np.asarray([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=np.float32),
            1,
            lambda size: iter([np.asarray([[0, 1, 2]], dtype=np.int64)]),
            np.arange(3),
            np.tile([0.0, 0.0, 1.0], (3, 1)),
        )
        key, header, body = split(encode(prepared, 64, 48, [None], False))
        deadline = time.monotonic() + 2
        request = BinaryRenderRequest(
            deadline=deadline,
            priority=Priority.INTERACTIVE,
            request_id="invalid-length",
            recipe="test",
            units=1,
            geometry_key=key,
            geometry_bytes=len(body) + int(fault == "declaration"),
            header=header,
        )
        with client._connect(client.directory(), deadline, start=False) as connection:
            send_admitted(connection, request.model_dump_json().encode(), deadline)
            response = json.loads(receive(connection, deadline))
            if fault in {"upload", "digest"}:
                assert response == {"geometry": "upload"}
                send(
                    connection,
                    body[:-1] if fault == "upload" else bytes([body[0] ^ 1]) + body[1:],
                )
                response = json.loads(receive(connection, deadline))
            assert response == {"error": "invalid_input"}


class TestHandlerAdmission:
    def test_refuses_excess_handlers_without_leaking_slots(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_fault_probe",
                str(tmp_path),
                "slots",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"accepted": 16, "released": 16}
        assert not (tmp_path / "runtime/compute/broker.sock").exists()


class TestWithdrawnInference:
    def test_does_not_execute_a_withdrawn_ready_request(self, tmp_path):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tests.fakes.compute_fault_probe",
                str(tmp_path),
                "withdraw",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        report = json.loads(result.stdout)
        assert report["order"] == ["background"]
        assert report["responses"] == [{"result": "cmVzdWx0"}, {"error": "deadline"}]
        assert report["cooled"] is False
