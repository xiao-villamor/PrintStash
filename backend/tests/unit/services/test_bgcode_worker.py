"""The child installs resource caps before passing literal paths to exec."""

from app.services import bgcode_worker


class TestConverterLauncher:
    def test_limits_are_installed_before_exec_with_literal_paths(self, monkeypatch):
        events = []
        monkeypatch.setattr(
            bgcode_worker.resource,
            "setrlimit",
            lambda kind, values: events.append((kind, values)),
        )
        monkeypatch.setattr(
            bgcode_worker.os,
            "execv",
            lambda executable, arguments: events.append((executable, arguments)),
        )
        bgcode_worker.main(
            ["/opt/converter", "/tmp/a file.bgcode", "536870912", "33554432", "30"]
        )
        assert events == [
            (bgcode_worker.resource.RLIMIT_AS, (536870912, 536870912)),
            (bgcode_worker.resource.RLIMIT_FSIZE, (33554432, 33554432)),
            (bgcode_worker.resource.RLIMIT_CPU, (30, 30)),
            (bgcode_worker.resource.RLIMIT_CORE, (0, 0)),
            ("/opt/converter", ["/opt/converter", "/tmp/a file.bgcode"]),
        ]
