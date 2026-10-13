"""Incremental foreground progress for the bounded fairness observer."""

from dataclasses import dataclass, field


@dataclass
class ForegroundProgress:
    _accepted: set[str] = field(default_factory=set, init=False)
    _pending: set[str] = field(default_factory=set, init=False)

    @property
    def pending(self) -> frozenset[str]:
        return frozenset(self._pending)

    @property
    def submitted(self) -> int:
        return len(self._accepted)

    @property
    def ready(self) -> int:
        return self.submitted - len(self._pending)

    def accept(self, job_id: str) -> None:
        if job_id in self._accepted:
            raise ValueError("fairness_duplicate_upload")
        self._accepted.add(job_id)
        self._pending.add(job_id)

    def complete(self, job_ids: set[str]) -> None:
        if not job_ids <= self._pending:
            raise ValueError("fairness_unaccepted_completion")
        self._pending.difference_update(job_ids)
