"""Work priority is a closed scoped value, independent of database imports."""

import pytest

from app.core.work_priority import WorkPriority, current_priority, priority_scope


class TestPriorityScope:
    def test_preserves_outer_priority(self):
        assert current_priority() is WorkPriority.INTERACTIVE
        with priority_scope(WorkPriority.BACKFILL):
            assert current_priority() is WorkPriority.BACKFILL
            with priority_scope(WorkPriority.INTERACTIVE):
                assert current_priority() is WorkPriority.INTERACTIVE
            assert current_priority() is WorkPriority.BACKFILL
        assert current_priority() is WorkPriority.INTERACTIVE

    def test_restores_priority_after_failure(self):
        with priority_scope(WorkPriority.BACKFILL):
            with pytest.raises(RuntimeError, match="step failed"):
                with priority_scope(WorkPriority.INTERACTIVE):
                    raise RuntimeError("step failed")
            assert current_priority() is WorkPriority.BACKFILL
        assert current_priority() is WorkPriority.INTERACTIVE

    @pytest.mark.parametrize("priority", ["interactive", "backfill", None, True, 1])
    def test_rejects_untyped_priority(self, priority):
        with pytest.raises(TypeError, match="WorkPriority"):
            with priority_scope(priority):
                pytest.fail("untyped priority entered a scope")

    def test_reexports_the_canonical_enum(self):
        from app.db.models.types import WorkPriority as PersistedPriority

        assert PersistedPriority is WorkPriority
