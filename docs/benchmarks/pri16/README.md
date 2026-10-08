# PRI-16 development evidence

The archived probe exercised candidate commit
969b296685bb6da41b5649d30c2cf3f2bb8bfeb4 with Python 3.14.8 and a physical
NVIDIA RTX 5060 through Vulkan, driver 610.88, on Windows. The report includes
the predeclared source hashes, versions, dimensions, immutable tolerances and all
60 observations. Sources are synthetic cube and torus controls from trimesh.
No private model or path is included.

| Control | Attempts | Completed | Equal alpha >=128 masks | Maximum RGBA difference | Stable completed output |
| --- | ---: | ---: | --- | ---: | --- |
| Cube | 30 | 30 | Yes | 0 | Yes |
| Torus with hole | 30 | 24 | Yes | 83 | Yes |

The torus exceeds the **8/255** channel gate. Six torus attempts failed with the
typed context_failed outcome. These failures remain in the raw report. Their
native root cause was not captured by this development probe and is unresolved.
There is no speedup qualification: the measurements omit supervised cleanup and
upload/publication, use prepared controls, run on Windows and had concurrent host
work. Do not compare their recorded phase timings as isolated performance samples.

**Decision: reject production server adoption of this candidate.** No tolerance
is relaxed. Stage B remains gated, with CPU production rendering unchanged. The
browser adapter is an independent implementation and decision. A revised server
candidate needs new clean-commit evidence, the full declared STL/3MF corpus,
independent protected-part assertions, physical recovery tests and both native
Linux/Docker verification devices.

To reproduce this historical probe, use the exact tested commit in a clean
checkout, install its mesh and webgpu-pilot dependencies on Python 3.14.8, and put
backend plus backend/packages/printstash-core/src on PYTHONPATH. Run
windows-controls.py from a new output directory on Windows. It refuses to
overwrite an existing manifest/sample set. The historical commit identifier in
the probe must not be used to label results from a different implementation.
This probe does not replace the supervised Linux qualification CLI.

Additional conformance checks:
- The disposable diagnostic image builds using Python 3.14.8.
- UID/GID 12345:12345 without device access refuses physical acceleration.
- Explicit software conformance passes in that image and reports acceleration=false.
- The available Docker daemon refuses --gpus all: no GPU vendor is configured.
- WSL sees llvmpipe, while native Windows enumerates RTX 5060 Vulkan and D3D12.
