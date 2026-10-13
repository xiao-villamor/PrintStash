# ADR-0009: Preserve 3MF precision behind an explicit scene reader

Status: Accepted — retain the bounded float64 XML reader with an explicit scene-reading owner and capability policy. Native distribution adoption remains a separate decision.

## Decision

Retain the bounded XML reader as the production geometry source while extracting
an explicit scene-reading contract. Lib3MF 2.5.0 remains an evaluated candidate;
its availability on ARM64 is not the reason for retaining the current reader.
Its float32 geometry/placement API and observed slicer compatibility differences
prevent replacing the existing float64 output without changing useful geometry.
Neither a production dependency nor an automatic parser fallback is introduced.

The production `three_mf_scene.read_scene` owner reads reachable geometry and
placements, while the compatibility bridge explicitly materializes the arrays
its existing consumers require. This separation does not yet make every
measurement, fingerprint or renderer operate on retained instances. Package,
traversal and expanded-face safety limits continue to apply before publication.

The [implemented capability policy](../3mf-capabilities.md) covers primary OPC
relationships, Core meshes and components, six physical units, and the Production
external model-part reference subset with nested nonsingular transforms and
reflections. It does not claim full Production extension conformance. Unknown
required namespaces are refused in every reached model part; undeclared required
prefixes are invalid input. Optional slicer metadata does not change geometry.
Legacy slicer `printable` filtering is a compatibility rule: the Core 1.4 schema
does not declare that build-item attribute. Unreachable model parts do not enter
measurements, although whole-package safety checks still apply. Surface bounds
use referenced vertices without rewriting source bytes.

## Distribution and licensing

Both candidates remain viable to investigate. The [official 2.5.0 distribution](https://pypi.org/project/lib3mf/2.5.0/)
has a `py3-none-manylinux2014_x86_64` wheel. The separately maintained
[py-lib3mf 2.5.0 distribution](https://pypi.org/project/py-lib3mf/2.5.0/)
has CPython 3.10–3.14 ARM64 wheels tagged manylinux 2.24/2.28 and publishing
attestations. Their generated `lib3mf/Lib3MF.py` files are byte-identical, but the
ARM package's `__init__.py` does not export the official top-level helpers.
`lib3mf.Lib3MF.Wrapper` is their common binding entry. They install the same module
and must not coexist in one environment.

The [upstream native library and generated binding](https://github.com/3MFConsortium/lib3mf/blob/v2.5.0/LICENSE)
use a two-clause BSD license. The [alternative packaging project](https://github.com/jdegenstein/py-lib3mf/blob/master/pyproject.toml)
declares Apache-2.0 metadata. Wheel notices differ: the official wheel has its
main LICENSE; the ARM wheel has a third-party notice file and the binding's BSD
header. Any distribution proposal must pin source/wheel hashes, preserve applicable
upstream and third-party notices, and verify the package entry, native version and
ABI in each target image. Conditional package pins are an option after those
gates; producing our own common wheels is another. Neither is selected yet.

## Follow-up gates

1. Keep the corpus declarations and implemented capability policy aligned.
   Production regressions now cover required-extension refusals and reachable
   geometry; expanding support requires equally explicit acceptance cases.
2. Preserve float64 resource coordinates and transforms, printable compatibility
   and real slicer availability. Do not silently recenter/rewrite source data to
   conceal native float32 loss.
3. Separate scene ownership from flattening, with measured unique-versus-expanded
   budgets and exact equivalence on the small analytic fixtures.
4. Run the pinned corpus and cold through-array measurements on actual amd64 and
   ARM64 target images before accepting either distribution. Recheck source and
   wheel notices as part of the concrete packaging change.
