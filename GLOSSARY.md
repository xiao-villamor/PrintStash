# PrintStash library presentation

The library's entity definitions remain authoritative in [CONTEXT.md](CONTEXT.md).
These terms name views of that library; they do not change ownership of Models.

## Language

**Everything**:
The library view containing ordinary Models and Multipart Models together.
A Model remains visible in its own right when a Multipart Model references it.
_Avoid_: Organized (a different, retiring presentation).

**Multipart Sets**:
The library view containing Multipart Models only.
A set references independently addressable Models without owning their Artifacts.
_Avoid_: Parts only (a different, retiring presentation).
