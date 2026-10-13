"""Host and process observation preserve conservative native ceilings."""

from pathlib import Path

import pytest

from app.modules.media import native_process


class TestDetectMemoryLimitBytes:
    @pytest.mark.parametrize(
        "membership",
        ["1:cpu:/slice", "0::/", "0::/../outside", "0::/" + "/".join(["slice"] * 129)],
    )
    def test_ignores_unusable_cgroup_membership(self, monkeypatch, membership):
        files = {
            "/proc/self/cgroup": membership,
            "/sys/fs/cgroup/memory.max": "max",
            "/proc/meminfo": "MemTotal:       8388608 kB\n",
        }

        def read(path, *args, **kwargs):
            if str(path) in files:
                return files[str(path)]
            raise OSError("absent")

        monkeypatch.setattr(Path, "read_text", read)

        assert native_process.memory_limit_bytes() == 8 * 1024**3

    @pytest.mark.parametrize(
        "parent,child,expected",
        [("max", "1073741824", 1073741824), ("536870912", "max", 536870912)],
        ids=["service-limit", "parent-slice-limit"],
    )
    def test_detects_effective_nested_cgroup_limit(
        self, monkeypatch, parent, child, expected
    ):
        files = {
            "/proc/self/cgroup": "0::/test.slice/printstash.service\n",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/test.slice/memory.max": parent,
            "/sys/fs/cgroup/test.slice/printstash.service/memory.max": child,
            "/proc/meminfo": "MemTotal:       8388608 kB\n",
        }

        def read(path, *args, **kwargs):
            try:
                return files[str(path)]
            except KeyError as exc:
                raise OSError("absent") from exc

        monkeypatch.setattr(Path, "read_text", read)

        assert native_process.memory_limit_bytes() == expected

    def test_detect_memory_limit_is_positive_on_linux(self) -> None:
        limit = native_process.memory_limit_bytes()
        # On Linux CI this reads /proc/meminfo or a cgroup; elsewhere it may be None.
        assert limit is None or limit > 0

    def test_detect_memory_limit_reads_cgroup_v2_value(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/sys/fs/cgroup/memory.max":
                return "2147483648\n"  # 2 GB
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        limit = native_process.memory_limit_bytes()
        assert limit is not None
        assert limit <= 2147483648

    def test_detect_memory_limit_reads_cgroup_v1_value(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/sys/fs/cgroup/memory.max":
                raise OSError("cgroup v2 absent")
            if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
                return "1073741824\n"  # 1 GB
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        limit = native_process.memory_limit_bytes()
        assert limit is not None
        assert limit <= 1073741824

    def test_detect_memory_limit_ignores_unlimited_cgroup_v2(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text

        def fake_read_text(self, *a, **k):
            if str(self) == "/sys/fs/cgroup/memory.max":
                return "max\n"
            if str(self) == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
                raise OSError("absent")
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        # Falls through to /proc/meminfo (real, host-dependent) or None.
        limit = native_process.memory_limit_bytes()
        assert limit is None or limit > 0

    def test_detect_memory_limit_survives_unreadable_sources(self, monkeypatch) -> None:
        from pathlib import Path as _Path

        real_read_text = _Path.read_text
        unreadable = {
            "/proc/self/cgroup",
            "/sys/fs/cgroup/memory.max",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes",
            "/proc/meminfo",
        }

        def fake_read_text(self, *a, **k):
            if str(self) in unreadable:
                raise OSError("no such file")
            return real_read_text(self, *a, **k)

        monkeypatch.setattr(_Path, "read_text", fake_read_text)
        assert native_process.memory_limit_bytes() is None


class TestNativeMemoryBudget:
    def test_retains_the_admitted_native_allowance(self, monkeypatch) -> None:
        from types import SimpleNamespace

        from app.runtime import native_runtime
        from app.runtime.native_admission import Resources

        monkeypatch.setattr(
            native_runtime,
            "current_permit",
            lambda: SimpleNamespace(resources=Resources(1, 3 * 1024**3)),
        )

        assert native_process.native_memory_budget_bytes() == 3 * 1024**3

    def test_undetected_capacity_remains_bounded(self, monkeypatch) -> None:
        monkeypatch.setattr(native_process, "memory_limit_bytes", lambda: None)

        assert native_process.native_memory_budget_bytes() == 2 * 1024**3
