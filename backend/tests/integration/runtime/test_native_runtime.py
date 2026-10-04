"""Bootstrap binding and nested native calls retain one concrete resource owner."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from app.runtime import native_runtime
from app.runtime.native_admission import AdmissionTooLarge, LocalResourcePool, Resources


@pytest.fixture
def pool(tmp_path):
    owner = LocalResourcePool(tmp_path)
    prior = native_runtime.bind_pool(owner)
    try:
        yield owner
    finally:
        native_runtime.bind_pool(prior)


class TestAdmit:
    def test_requires_explicit_bootstrap_binding(self):
        prior = native_runtime.bind_pool(None)
        try:
            with pytest.raises(RuntimeError, match="not bound"):
                with native_runtime.admit(
                    Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
                ):
                    pytest.fail("unbound infrastructure was constructed implicitly")
        finally:
            native_runtime.bind_pool(prior)

    def test_nested_work_retains_the_same_permit(self, pool):
        with native_runtime.admit(
            Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
        ) as outer:
            with native_runtime.admit(
                Resources(1, 20), Resources(2, 100), checkpoint=lambda: None
            ) as nested:
                assert nested is outer
                assert native_runtime.current_permit() is outer
        assert native_runtime.current_permit() is None

    def test_nested_work_cannot_expand_its_memory_share(self, pool):
        with native_runtime.admit(
            Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
        ):
            with pytest.raises(AdmissionTooLarge, match="nested work"):
                with native_runtime.admit(
                    Resources(1, 80), Resources(2, 100), checkpoint=lambda: None
                ):
                    pytest.fail("nested work expanded its reservation")

    def test_other_threads_get_independent_permits(self, pool):
        def independent():
            with native_runtime.admit(
                Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
            ) as permit:
                return permit.fileno

        with native_runtime.admit(
            Resources(1, 40), Resources(2, 100), checkpoint=lambda: None
        ) as outer:
            with ThreadPoolExecutor(1) as executor:
                assert executor.submit(independent).result(timeout=5) != outer.fileno

    def test_exception_releases_current_binding(self, pool):
        with pytest.raises(OSError, match="native failed"):
            with native_runtime.admit(
                Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
            ):
                raise OSError("native failed")
        with native_runtime.admit(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as permit:
            assert permit.resources == Resources(1, 100)


class TestInherit:
    def test_borrows_the_bootstrap_descriptor(self, pool):
        with pool.reserve(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as original:
            with native_runtime.inherit(original.fileno, original.path) as inherited:
                assert native_runtime.current_permit() is inherited
                assert inherited.resources == Resources(1, 100)
                assert inherited.fileno != original.fileno
        assert native_runtime.current_permit() is None

    def test_refuses_to_replace_an_active_scope(self, pool):
        with native_runtime.admit(
            Resources(1, 100), Resources(1, 100), checkpoint=lambda: None
        ) as original:
            with pytest.raises(RuntimeError, match="already owns"):
                with native_runtime.inherit(original.fileno, original.path):
                    pytest.fail("active scope was replaced")
            assert native_runtime.current_permit() is original
