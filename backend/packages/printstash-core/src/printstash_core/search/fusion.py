"""Rank fusion uses ordinal relevance, never compares unrelated embedding scores."""

from dataclasses import dataclass
from math import isfinite

from .passages import SearchSubject


@dataclass(frozen=True)
class RankedLeg:
    name: str
    subjects: tuple[SearchSubject, ...]
    weight: float = 1.0


@dataclass(frozen=True)
class FusedMatch:
    subject: SearchSubject
    score: float
    legs: tuple[str, ...]


def fuse(
    legs: tuple[RankedLeg, ...], *, k: int = 60, limit: int = 2048
) -> tuple[FusedMatch, ...]:
    if (
        type(k) is not int
        or not 1 <= k <= 1000
        or not 1 <= limit <= 2048
        or len(legs) > 8
        or len({leg.name for leg in legs}) != len(legs)
        or any(
            not leg.name
            or len(leg.subjects) > 2048
            or not isfinite(leg.weight)
            or not 0 < leg.weight <= 10
            for leg in legs
        )
    ):
        raise ValueError("search_fusion_invalid")
    scores: dict[SearchSubject, float] = {}
    contributions: dict[SearchSubject, list[str]] = {}
    for leg in legs:
        seen = set()
        for subject in leg.subjects:
            if subject in seen:
                continue
            seen.add(subject)
            scores[subject] = scores.get(subject, 0.0) + leg.weight / (k + len(seen))
            contributions.setdefault(subject, []).append(leg.name)
    ordered = sorted(
        scores,
        key=lambda subject: (
            -scores[subject],
            subject.subject_type.value,
            subject.subject_id,
        ),
    )[:limit]
    return tuple(
        FusedMatch(subject, scores[subject], tuple(contributions[subject]))
        for subject in ordered
    )
