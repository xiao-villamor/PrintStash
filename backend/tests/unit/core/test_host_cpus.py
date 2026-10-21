"""The native mesh job default follows the CPUs a process can really use.

A container limited with ``--cpus`` still sees every host core through
``os.process_cpu_count()``. If the cgroup quota is ignored, a 4-CPU container on
a 12-core host defaults to the cap and oversubscribes; if the default ignores
CPUs entirely, one mesh job runs at a time on any machine and an import uses a
fraction of the CPU it was given. Both regressions turn this file red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import host_cpus
from app.core.host_cpus import cgroup_cpu_limit, native_jobs_for, usable_cpus

CONTAINER_CGROUP = "0::/\n"
SERVICE_CGROUP = "0::/system.slice/printstash.service\n"


@pytest.fixture
def cgroup(tmp_path: Path) -> Path:
    root = tmp_path / "cgroup"
    root.mkdir()
    return root


def _self_cgroup(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "self-cgroup"
    path.write_text(content)
    return path


def _cpu_max(group: Path, line: str) -> None:
    group.mkdir(parents=True, exist_ok=True)
    (group / "cpu.max").write_text(line + "\n")


def _v1_quota(group: Path, quota: int, period: int = 100000) -> None:
    group.mkdir(parents=True, exist_ok=True)
    (group / "cpu.cfs_quota_us").write_text(f"{quota}\n")
    (group / "cpu.cfs_period_us").write_text(f"{period}\n")


class TestCgroupCpuLimit:
    def test_reads_the_container_quota(self, cgroup: Path, tmp_path: Path) -> None:
        _cpu_max(cgroup, "400000 100000")

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) == 4

    def test_an_unlimited_quota_is_no_limit(self, cgroup: Path, tmp_path: Path) -> None:
        _cpu_max(cgroup, "max 100000")

        assert (
            cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) is None
        )

    @pytest.mark.parametrize(
        ("line", "cpus"),
        [
            pytest.param("150000 100000", 2, id="one-and-a-half"),
            pytest.param("50000 100000", 1, id="half-a-cpu"),
        ],
    )
    def test_a_fractional_quota_rounds_up(
        self, cgroup: Path, tmp_path: Path, line: str, cpus: int
    ) -> None:
        _cpu_max(cgroup, line)

        assert (
            cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) == cpus
        )

    def test_a_service_group_quota_below_the_mount_applies(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        _cpu_max(cgroup, "max 100000")
        _cpu_max(cgroup / "system.slice" / "printstash.service", "200000 100000")

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, SERVICE_CGROUP)) == 2

    def test_a_parent_slice_quota_applies(self, cgroup: Path, tmp_path: Path) -> None:
        _cpu_max(cgroup / "system.slice", "300000 100000")
        _cpu_max(cgroup / "system.slice" / "printstash.service", "max 100000")

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, SERVICE_CGROUP)) == 3

    def test_the_tightest_quota_on_the_path_wins(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        _cpu_max(cgroup / "system.slice", "200000 100000")
        _cpu_max(cgroup / "system.slice" / "printstash.service", "600000 100000")

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, SERVICE_CGROUP)) == 2

    def test_reads_a_cgroup_v1_quota(self, cgroup: Path, tmp_path: Path) -> None:
        (cgroup / "cpu").mkdir()
        (cgroup / "cpu" / "cpu.cfs_quota_us").write_text("300000\n")
        (cgroup / "cpu" / "cpu.cfs_period_us").write_text("100000\n")

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) == 3

    def test_a_cgroup_v1_quota_of_minus_one_is_no_limit(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        (cgroup / "cpu").mkdir()
        (cgroup / "cpu" / "cpu.cfs_quota_us").write_text("-1\n")
        (cgroup / "cpu" / "cpu.cfs_period_us").write_text("100000\n")

        assert (
            cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) is None
        )

    def test_no_cgroup_files_is_no_limit(self, cgroup: Path, tmp_path: Path) -> None:
        assert cgroup_cpu_limit(cgroup, tmp_path / "absent") is None

    def test_ignores_a_cgroup_path_that_escapes_the_mount(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        _cpu_max(tmp_path / "outside", "100000 100000")

        assert (
            cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, "0::/../outside\n")) is None
        )

    @pytest.mark.parametrize("controllers", ["cpu", "cpu,cpuacct", "cpuacct,cpu"])
    def test_reads_the_v1_process_cpu_group(
        self, cgroup: Path, tmp_path: Path, controllers: str
    ) -> None:
        mount = cgroup / controllers
        _v1_quota(mount, -1)
        _v1_quota(mount / "system.slice" / "printstash.service", 200000)
        membership = f"2:{controllers}:/system.slice/printstash.service\n"

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, membership)) == 2

    def test_a_v1_parent_quota_applies(self, cgroup: Path, tmp_path: Path) -> None:
        mount = cgroup / "cpu"
        _v1_quota(mount / "system.slice", 300000)
        _v1_quota(mount / "system.slice" / "printstash.service", -1)
        membership = "2:cpu:/system.slice/printstash.service\n"

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, membership)) == 3

    def test_the_tightest_v1_quota_on_the_path_wins(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        mount = cgroup / "cpu"
        _v1_quota(mount, 600000)
        _v1_quota(mount / "system.slice", 200000)
        _v1_quota(mount / "system.slice" / "printstash.service", 400000)
        membership = "2:cpu:/system.slice/printstash.service\n"

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, membership)) == 2

    def test_reads_a_namespace_root_v1_quota(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        _v1_quota(cgroup / "cpu", 300000)

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, "2:cpu:/\n")) == 3

    def test_ignores_other_v1_controller_memberships(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        mount = cgroup / "cpu"
        _v1_quota(mount / "accounting", 100000)
        _v1_quota(mount / "affinity", 100000)
        _v1_quota(mount / "service", 600000)
        _v1_quota(cgroup / "cpuacct" / "accounting", 100000)
        membership = "2:cpuacct:/accounting\n3:cpuset:/affinity\n4:cpu:/service\n"

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, membership)) == 6

    def test_an_unlimited_v1_path_is_no_limit(
        self, cgroup: Path, tmp_path: Path
    ) -> None:
        mount = cgroup / "cpu"
        _v1_quota(mount, -1)
        _v1_quota(mount / "system.slice", -1)
        _v1_quota(mount / "system.slice" / "printstash.service", -1)
        membership = "2:cpu:/system.slice/printstash.service\n"

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, membership)) is None

    @pytest.mark.parametrize("membership", ["/../outside", "outside"])
    def test_ignores_an_invalid_v1_membership_path(
        self, cgroup: Path, tmp_path: Path, membership: str
    ) -> None:
        _v1_quota(cgroup / "outside", 100000)
        _v1_quota(cgroup / "cpu" / "outside", 100000)
        process_group = _self_cgroup(tmp_path, f"2:cpu:{membership}\n")

        assert cgroup_cpu_limit(cgroup, process_group) is None

    @pytest.mark.parametrize(("v1", "v2"), [(2, 4), (4, 2)])
    def test_the_tightest_available_controller_quota_wins(
        self, cgroup: Path, tmp_path: Path, v1: int, v2: int
    ) -> None:
        _v1_quota(cgroup / "cpu" / "service", v1 * 100000)
        _cpu_max(cgroup / "service", f"{v2 * 100000} 100000")
        membership = "0::/service\n2:cpu:/service\n"

        assert cgroup_cpu_limit(cgroup, _self_cgroup(tmp_path, membership)) == 2


class TestUsableCpus:
    def test_a_quota_below_the_visible_cpus_wins(
        self, cgroup: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(host_cpus.os, "process_cpu_count", lambda: 12)
        _cpu_max(cgroup, "400000 100000")

        assert usable_cpus(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) == 4

    def test_visible_cpus_below_the_quota_win(
        self, cgroup: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(host_cpus.os, "process_cpu_count", lambda: 2)
        _cpu_max(cgroup, "800000 100000")

        assert usable_cpus(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) == 2

    def test_without_a_quota_every_visible_cpu_counts(
        self, cgroup: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(host_cpus.os, "process_cpu_count", lambda: 6)

        assert usable_cpus(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP)) == 6

    def test_an_unknown_cpu_count_raises(
        self, cgroup: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(host_cpus.os, "process_cpu_count", lambda: None)

        with pytest.raises(RuntimeError, match="no CPU count"):
            usable_cpus(cgroup, _self_cgroup(tmp_path, CONTAINER_CGROUP))

    @pytest.mark.parametrize("version", ["v1", "v2"])
    @pytest.mark.parametrize(
        ("quota", "cpus"),
        [
            (300000, 3),
            (310000, 4),
            (400000, 4),
            (510000, 6),
            (710000, 8),
        ],
    )
    def test_fractional_quotas_round_up_to_whole_cpus(
        self,
        cgroup: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        version: str,
        quota: int,
        cpus: int,
    ) -> None:
        monkeypatch.setattr(host_cpus.os, "process_cpu_count", lambda: 16)
        if version == "v1":
            _v1_quota(cgroup / "cpu" / "service", quota)
            membership = "2:cpu:/service\n"
        else:
            _cpu_max(cgroup / "service", f"{quota} 100000")
            membership = "0::/service\n"
        process_group = _self_cgroup(tmp_path, membership)

        assert usable_cpus(cgroup, process_group) == cpus


class TestNativeJobsFor:
    @pytest.mark.parametrize(
        ("cpus", "pool_gib", "jobs"),
        [
            pytest.param(1, 2, 2, id="one-cpu"),
            pytest.param(2, 2, 3, id="two-cpus-four-gib-host"),
            pytest.param(3, 3, 4, id="three-cpus-six-gib-host"),
            pytest.param(4, 4, 5, id="four-cpus-eight-gib-host"),
        ],
    )
    def test_runs_one_job_per_cpu_plus_one(
        self, cpus: int, pool_gib: int, jobs: int
    ) -> None:
        assert native_jobs_for(cpus, pool_gib * 1024**3) == jobs

    @pytest.mark.parametrize(
        ("cpus", "pool_mib", "jobs"),
        [
            pytest.param(4, 2048, 3, id="four-cpus-two-gib-pool"),
            pytest.param(8, 1280, 2, id="two-floors"),
            pytest.param(8, 512, 1, id="below-one-floor"),
        ],
    )
    def test_runs_no_more_jobs_than_fit_the_pool(
        self, cpus: int, pool_mib: int, jobs: int
    ) -> None:
        assert native_jobs_for(cpus, pool_mib * 1024**2) == jobs

    def test_stays_at_the_default_cap(self) -> None:
        assert native_jobs_for(64, 64 * 1024**3) == host_cpus.MAX_DEFAULT_NATIVE_JOBS

    def test_rejects_a_count_below_one_cpu(self) -> None:
        with pytest.raises(ValueError, match="at least one CPU"):
            native_jobs_for(0, 1024**3)

    def test_rejects_an_empty_pool(self) -> None:
        with pytest.raises(ValueError, match="at least one byte"):
            native_jobs_for(2, 0)


class TestDefaultNativeJobs:
    def test_sizes_the_pool_from_the_detected_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import host_memory

        monkeypatch.setattr(host_memory, "memory_limit_bytes", lambda: 4 * 1024**3)
        monkeypatch.setattr(host_cpus, "usable_cpus", lambda: 4)

        assert host_cpus.default_native_jobs(0.5) == 3

    def test_follows_the_configured_mesh_fraction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import host_memory

        monkeypatch.setattr(host_memory, "memory_limit_bytes", lambda: 4 * 1024**3)
        monkeypatch.setattr(host_cpus, "usable_cpus", lambda: 4)

        assert host_cpus.default_native_jobs(0.25) == 1

    def test_keeps_half_the_memory_when_estimates_are_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import host_memory

        monkeypatch.setattr(host_memory, "memory_limit_bytes", lambda: 4 * 1024**3)
        monkeypatch.setattr(host_cpus, "usable_cpus", lambda: 4)

        assert host_cpus.default_native_jobs(0) == 3

    def test_assumes_the_undetected_pool_without_a_memory_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import host_memory

        monkeypatch.setattr(host_memory, "memory_limit_bytes", lambda: None)
        monkeypatch.setattr(host_cpus, "usable_cpus", lambda: 8)

        assert host_cpus.default_native_jobs(0.5) == 3


class TestFittingNativeJobs:
    @pytest.mark.parametrize(
        ("pool", "jobs"),
        [
            pytest.param(64 * 1024**3, 102, id="large-pool-has-no-cpu-cap"),
            pytest.param(2 * 1024**3, 3, id="two-gib"),
            pytest.param(1, 1, id="smaller-than-one-floor-still-runs-one"),
        ],
    )
    def test_counts_the_thumbnail_floors_a_pool_fits(
        self, pool: int, jobs: int
    ) -> None:
        assert host_cpus.fitting_native_jobs(pool) == jobs

    def test_rejects_an_empty_pool(self) -> None:
        with pytest.raises(ValueError, match="at least one byte"):
            host_cpus.fitting_native_jobs(0)


class TestNativePoolBytes:
    def test_takes_the_fraction_of_the_detected_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import host_memory

        monkeypatch.setattr(host_memory, "memory_limit_bytes", lambda: 8 * 1024**3)

        assert host_cpus.native_pool_bytes(0.25) == 2 * 1024**3

    def test_assumes_the_undetected_pool_without_a_memory_limit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.core import host_memory

        monkeypatch.setattr(host_memory, "memory_limit_bytes", lambda: None)

        assert host_cpus.native_pool_bytes(0.5) == host_cpus.UNDETECTED_POOL_BYTES


class TestInferenceJobsFor:
    @pytest.mark.parametrize(
        ("cpus", "jobs"),
        [
            pytest.param(1, 1, id="one-cpu"),
            pytest.param(3, 1, id="three-cpus"),
            pytest.param(4, 2, id="four-cpus"),
            pytest.param(64, 4, id="sixty-four-cpus-stays-at-the-cap"),
        ],
    )
    def test_runs_one_inference_per_two_cpus_within_bounds(
        self, cpus: int, jobs: int
    ) -> None:
        assert host_cpus.inference_jobs_for(cpus) == jobs

    def test_rejects_a_count_below_one_cpu(self) -> None:
        with pytest.raises(ValueError, match="at least one CPU"):
            host_cpus.inference_jobs_for(0)
