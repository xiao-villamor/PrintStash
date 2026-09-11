"""Relative measurements may be rebased only from a known common reference."""

import math


def rebase_scale(
    factor: float | None,
    reference: float | None,
    *,
    same_reference: bool,
) -> float | None:
    if not same_reference or factor is None or reference is None or reference <= 0:
        return None
    result = factor / reference
    return result if math.isfinite(result) and result > 0 else None
