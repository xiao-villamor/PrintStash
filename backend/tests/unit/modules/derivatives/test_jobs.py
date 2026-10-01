"""Producer errors retain their identity unless live policy refused admission."""

import pytest

from app.core.errors import OperationError
from app.db.models import WorkPriority
from app.modules.derivatives import jobs
from app.modules.derivatives.kinds import GROUPS
from app.modules.work.runner import ExecutionContext


class TestProducerErrors:
    @pytest.mark.parametrize(
        "derivative_group", GROUPS, ids=lambda group: group.definition.value
    )
    def test_an_unrelated_operation_error_remains_a_failure(self, derivative_group):
        failure = OperationError("storage_unavailable")

        def refuse(_file_id):
            raise failure

        context = ExecutionContext(
            "job",
            derivative_group.definition,
            "file/1",
            WorkPriority.BACKFILL,
            "attempt",
            1,
        )
        with pytest.raises(OperationError, match="storage_unavailable") as raised:
            jobs._step(refuse, derivative_group)(context)
        assert raised.value is failure
