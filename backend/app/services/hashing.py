"""Compatibility facade for framework-neutral hashing helpers."""

from printstash_core.files import sha256_file as sha256_file
from printstash_core.files import sha256_stream as sha256_stream

__all__ = ["sha256_file", "sha256_stream"]
