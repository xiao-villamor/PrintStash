"""A sudden allocation used by the production-container containment gate."""

allocation = bytearray(8 * 1024**3)
raise AssertionError(f"unbounded worker allocated {len(allocation)} bytes")
