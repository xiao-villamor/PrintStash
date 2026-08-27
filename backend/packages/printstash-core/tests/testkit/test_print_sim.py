"""Defends ``test_print_sim_uses_injected_clock`` behavior for the ``testkit`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

from printstash_core_testkit import (
    COMPLETE,
    PAUSED,
    PRINTING,
    PrintSim,
    Received,
    Recorder,
    build_provider_app,
    start_server,
)
from printstash_core_testkit.mock_octoprint import create_app as create_octoprint_app
from printstash_core_testkit.mock_printer import create_app as create_moonraker_app
from printstash_core_testkit.mock_prusalink import create_app as create_prusalink_app


def test_print_sim_uses_injected_clock() -> None:
    now = [10.0]
    sim = PrintSim(
        total_mm=100.0,
        total_seconds=20.0,
        print_seconds=10.0,
        monotonic=lambda: now[0],
    )
    sim.start("part.gcode")
    now[0] = 12.5
    assert sim.state == PRINTING
    assert sim.progress() == 0.25
    sim.pause()
    assert sim.state == PAUSED
    now[0] = 20.0
    assert sim.progress() == 0.25
    sim.resume()
    now[0] = 27.5
    assert sim.progress() == 1.0
    assert sim.state == COMPLETE


def test_recorder_returns_copies_and_counts_calls() -> None:
    recorder = Recorder()
    received = Received("webhook", "POST", "/event", {})
    recorder.record(received)
    items = recorder.all()
    items.clear()
    assert recorder.for_target("webhook") == [received]
    assert recorder.bump("retry") == 1
    assert recorder.bump("retry") == 2


def test_contract_apps_are_available_from_the_shared_testkit() -> None:
    recorder = Recorder()
    provider_app = build_provider_app(recorder)
    moonraker_app, _ = create_moonraker_app()
    prusalink_app, _ = create_prusalink_app()
    octoprint_app, _ = create_octoprint_app()

    assert provider_app.routes
    assert moonraker_app.routes
    assert prusalink_app.routes
    assert octoprint_app.routes


def test_contract_server_runs_on_a_real_loopback_socket() -> None:
    app, _ = create_octoprint_app()
    server = start_server(app)
    try:
        assert server.base_url == f"http://127.0.0.1:{server.port}"
        assert server.port > 0
    finally:
        server.stop()
