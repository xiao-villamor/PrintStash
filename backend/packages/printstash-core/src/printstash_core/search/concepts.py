"""Small printable-object vocabulary, independent of name spelling recovery.

Phrases require every token. A screw is only a bolt alternative when metadata
also describes its thread; generic words alone must not expand the search.
"""

from printstash_core.search.lexical import query_terms

_CONCEPTS = {
    "holder": (("cradle",), ("stand",), ("mount",), ("dock",)),
    "gear": (("toothed", "wheel"),),
    "bolt": (("threaded", "screw"),),
}


def alternatives(query: str) -> tuple[tuple[str, ...], ...]:
    tokens = query_terms(query)
    return _CONCEPTS.get(tokens[0], ()) if len(tokens) == 1 else ()
