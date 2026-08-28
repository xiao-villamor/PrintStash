"""The shared test kit: printer emulators, a print simulator, and a recorder.

`printstash_core_testkit` is shipped as its own package so the backend's contract
tier and this package's tests use the *same* fakes. That is the point: a
contract test is only worth running if the fake it runs against enforces the same
protocol the real printer does, and two separately-maintained copies of a fake
diverge quietly. So the kit is production code for testing purposes, and it gets
tested like production code.

Three pieces, three reasons:

**`PrintSim` takes an injected clock.** A print simulator that read the wall clock
would make every test that advances a print either slow or flaky. The clock is a
parameter, so a test moves time by assignment — and the state machine's
transitions (a pause freezing progress, a resume continuing from where it
stopped) are exact rather than approximate.

**`Recorder` hands out copies.** A test asserts on what a fake received; if the
recorder returned its own list, a test that filtered or cleared the result would
corrupt the next assertion in the same test.

**`start_server` binds a real loopback socket.** Contract tests exist to exercise
real HTTP — connection handling, headers, status codes — so an in-process
transport would defeat their whole purpose.
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


def _sim(now: list[float]) -> PrintSim:
    return PrintSim(
        total_mm=100.0,
        total_seconds=20.0,
        print_seconds=10.0,
        monotonic=lambda: now[0],
    )


class TestPrintSim:
    def test_reports_progress_from_the_injected_clock(self) -> None:
        now = [10.0]
        sim = _sim(now)
        sim.start("part.gcode")

        now[0] = 12.5

        # 2.5s of a 10s print. Reading the wall clock instead would make every
        # test that advances a print slow or flaky.
        assert sim.state == PRINTING
        assert sim.progress() == 0.25

    def test_freezes_progress_while_paused(self) -> None:
        now = [10.0]
        sim = _sim(now)
        sim.start("part.gcode")
        now[0] = 12.5
        sim.pause()

        now[0] = 20.0

        # A pause that kept accruing progress would let a test's paused printer
        # silently finish.
        assert sim.state == PAUSED
        assert sim.progress() == 0.25

    def test_continues_from_where_it_paused(self) -> None:
        now = [10.0]
        sim = _sim(now)
        sim.start("part.gcode")
        now[0] = 12.5
        sim.pause()
        now[0] = 20.0
        sim.resume()

        now[0] = 27.5

        assert sim.progress() == 1.0

    def test_reports_full_progress_once_the_print_time_has_elapsed(self) -> None:
        now = [10.0]
        sim = _sim(now)
        sim.start("part.gcode")

        now[0] = 20.0

        assert sim.progress() == 1.0

    def test_turns_over_to_complete_when_progress_is_read(self) -> None:
        now = [10.0]
        sim = _sim(now)
        sim.start("part.gcode")
        now[0] = 20.0

        sim.progress()

        # The transition is lazy: it happens inside `progress()`, not on a
        # timer. A test that advances the clock and then reads `state` alone
        # still sees `printing` — which is worth knowing, because it looks like
        # a bug in the simulator rather than in the test.
        assert sim.state == COMPLETE

    def test_does_not_turn_over_until_progress_is_read(self) -> None:
        now = [10.0]
        sim = _sim(now)
        sim.start("part.gcode")

        now[0] = 20.0

        assert sim.state == PRINTING


class TestRecorder:
    def test_records_what_a_fake_received(self) -> None:
        recorder = Recorder()
        received = Received("webhook", "POST", "/event", {})

        recorder.record(received)

        assert recorder.for_target("webhook") == [received]

    def test_hands_out_a_copy_of_its_history(self) -> None:
        recorder = Recorder()
        received = Received("webhook", "POST", "/event", {})
        recorder.record(received)

        recorder.all().clear()

        # A test that filters or clears the returned list must not corrupt the
        # next assertion in the same test.
        assert recorder.for_target("webhook") == [received]

    def test_reports_nothing_for_a_target_that_received_nothing(self) -> None:
        assert Recorder().for_target("webhook") == []

    def test_counts_repeat_calls_by_name(self) -> None:
        recorder = Recorder()

        # Used by the flaky-endpoint fakes to fail the first N attempts and then
        # succeed, which is how retry behaviour is tested without patching.
        assert recorder.bump("retry") == 1
        assert recorder.bump("retry") == 2


class TestProviderApps:
    def test_builds_a_notification_provider_app(self) -> None:
        assert build_provider_app(Recorder()).routes

    def test_builds_a_moonraker_emulator(self) -> None:
        app, _ = create_moonraker_app()

        assert app.routes

    def test_builds_a_prusalink_emulator(self) -> None:
        app, _ = create_prusalink_app()

        assert app.routes

    def test_builds_an_octoprint_emulator(self) -> None:
        app, _ = create_octoprint_app()

        assert app.routes


class TestStartServer:
    def test_serves_the_app_on_a_real_loopback_socket(self) -> None:
        app, _ = create_octoprint_app()
        server = start_server(app)

        try:
            # A real socket, not an in-process transport: contract tests exist
            # to exercise actual HTTP, and the port is chosen by the OS so
            # parallel workers cannot collide.
            assert server.base_url == f"http://127.0.0.1:{server.port}"
            assert server.port > 0
        finally:
            server.stop()
