"""Bounded name spelling helpers; descriptive similarity remains a separate signal."""

from string import ascii_lowercase, digits


def one_edit_apart(left: str, right: str) -> bool:
    """Accept exactly one insertion, deletion, substitution or adjacent swap."""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    at = next(
        (i for i, (a, b) in enumerate(zip(left, right, strict=False)) if a != b),
        len(left),
    )
    if len(left) < len(right):
        return left[at:] == right[at + 1 :]
    return left[at + 1 :] == right[at + 1 :] or (
        at + 1 < len(left)
        and left[at] == right[at + 1]
        and left[at + 1] == right[at]
        and left[at + 2 :] == right[at + 2 :]
    )


def candidate_prefixes(token: str) -> tuple[str, ...]:
    """Two-character vocabulary index probes for ordinary name misspellings.

    Preserve Unicode tokens while bounding replacement characters to Latin
    letters, digits and the query's characters. No wildcard dictionary scan.
    """
    if len(token) < 5:
        return ()
    alphabet = set(ascii_lowercase + digits + token)
    prefixes = {token[:2], token[1:3], token[0] + token[2], token[1] + token[0]}
    prefixes.update(char + token[1] for char in alphabet)
    prefixes.update(token[0] + char for char in alphabet)
    prefixes.update(char + token[0] for char in alphabet)
    return tuple(sorted(prefixes))
