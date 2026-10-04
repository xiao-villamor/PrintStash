"""Observe native queue time independently of execution and nested workers."""

from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from time import monotonic_ns

from printstash_core.inference import EmbeddingError

from app.core.cancellation import OperationCancelled
from app.modules.media.mesh_observability import record_admission
from app.modules.media.mesh_telemetry import AdmissionOutcome, AdmissionStats
from app.runtime.native_admission import NativePermit, Resources
from app.runtime.native_runtime import admit, current_permit


@contextmanager
def admission(
    request: Resources,
    capacity: Resources,
    *,
    checkpoint: Callable[[], None],
) -> Iterator[NativePermit]:
    """Record one parent admission; a worker's inherited scope is already admitted."""
    if current_permit() is not None:
        with admit(request, capacity, checkpoint=checkpoint) as permit:
            yield permit
        return
    started = monotonic_ns()
    outcome = AdmissionOutcome.FAILED
    with ExitStack() as owned:
        try:
            permit = owned.enter_context(
                admit(request, capacity, checkpoint=checkpoint)
            )
        except OperationCancelled:
            outcome = AdmissionOutcome.CANCELLED
            raise
        except EmbeddingError as exc:
            if exc.code == "inference_timeout":
                outcome = AdmissionOutcome.DEADLINE
            elif exc.code == "inference_cancelled":
                outcome = AdmissionOutcome.CANCELLED
            raise
        else:
            outcome = AdmissionOutcome.ADMITTED
        finally:
            record_admission(
                AdmissionStats(
                    monotonic_ns() - started,
                    request.slots,
                    request.bytes,
                    capacity.slots,
                    capacity.bytes,
                    outcome,
                )
            )
        # Execution failures must not be reported as queue failures or inflate
        # queue duration. ExitStack retains the permit throughout the work.
        yield permit
