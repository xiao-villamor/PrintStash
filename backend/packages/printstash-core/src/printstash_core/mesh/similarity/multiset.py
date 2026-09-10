"""Component containment from verified edges, with quantities rather than sets."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class Containment:
    contained_side: str
    copies: int
    evidence_class: str
    assignments: tuple[tuple[int, int, int], ...]


def component_containment(
    left: Mapping[int, int],
    right: Mapping[int, int],
    edges: Sequence[tuple[int, int]],
) -> Containment | None:
    """Consume only geometrically verified edges; never infer transitive identity."""
    if (
        not left
        or not right
        or len(left) > 256
        or len(right) > 256
        or len(edges) > 5120
    ):
        raise ValueError("component_budget_invalid")
    if (
        any(
            type(value) is not int or value < 1
            for value in (*left.values(), *right.values())
        )
        or max(sum(left.values()), sum(right.values())) > 2048
    ):
        raise ValueError("component_quantity_invalid")
    if any(a not in left or b not in right for a, b in edges):
        raise ValueError("component_edge_invalid")
    a_count, b_count = sum(left.values()), sum(right.values())
    if a_count == b_count:
        return None
    reverse = a_count > b_count
    source, target = (right, left) if reverse else (left, right)
    oriented = [(b, a) if reverse else (a, b) for a, b in sorted(set(edges))]
    base = _assign(source, target, oriented)
    if base is None:
        return None
    copies = sum(target.values()) // sum(source.values())
    plate = (
        sum(target.values()) % sum(source.values()) == 0
        and _assign(
            {key: count * copies for key, count in source.items()}, target, oriented
        )
        is not None
    )
    assignment = tuple(
        (b, a, count) if reverse else (a, b, count) for a, b, count in base
    )
    return Containment(
        "b" if reverse else "a",
        copies if plate else 1,
        "plate_of" if plate else "component_of",
        assignment,
    )


Node = tuple[str, int]


def _assign(
    source: Mapping[int, int],
    target: Mapping[int, int],
    edges: Sequence[tuple[int, int]],
) -> tuple[tuple[int, int, int], ...] | None:
    # Integral max-flow with a residual graph handles ambiguous equal components
    # without a greedy choice consuming the only match for a later component.
    origin, sink = ("start", 0), ("end", 0)
    residual: dict[tuple[Node, Node], int] = {}
    neighbors: dict[Node, list[Node]] = {}

    def link(a: Node, b: Node, capacity: int) -> None:
        residual[a, b] = capacity
        residual[b, a] = 0
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)

    for key, count in sorted(source.items()):
        link(origin, ("a", key), count)
    for a, b in edges:
        link(("a", a), ("b", b), 2048)
    for key, count in sorted(target.items()):
        link(("b", key), sink, count)
    flow = 0
    while flow < sum(source.values()):
        parents: dict[Node, Node | None] = {origin: None}
        queue = deque([origin])
        while queue and sink not in parents:
            node = queue.popleft()
            for neighbor in neighbors[node]:
                if neighbor not in parents and residual[node, neighbor] > 0:
                    parents[neighbor] = node
                    queue.append(neighbor)
        if sink not in parents:
            return None
        amount = 2048
        node = sink
        while (parent := parents[node]) is not None:
            amount = min(amount, residual[parent, node])
            node = parent
        node = sink
        while (parent := parents[node]) is not None:
            residual[parent, node] -= amount
            residual[node, parent] += amount
            node = parent
        flow += amount
    return tuple(
        (a, b, residual[("b", b), ("a", a)])
        for a, b in edges
        if residual[("b", b), ("a", a)] > 0
    )
