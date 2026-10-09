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

## Revised candidate

Candidate 649cd8f27af0c523c686662db129fac520e8da1f changes GPU readback to
winning face identifiers. Canonical interpolation uses original geometry
precision before the existing CPU shading and quantization. A diagnostic on
the torus located the old difference: raw supersampled channels differed by
at most one, while alpha-aware resizing amplified edge differences to 83.
The new approach does not change that resize policy or its tolerance.

| Control | Attempts | Completed | Equal foreground masks | Maximum RGBA difference | Stable output |
| --- | ---: | ---: | --- | ---: | --- |
| Cube | 30 | 30 | Yes | 0 | Yes |
| Torus with hole | 30 | 30 | Yes | 0 | Yes |

All sixty observations on 649cd8f2 pass these control quality checks. The
predeclared manifest and all samples are in windows-rtx5060-649cd8f2.json.
windows-revised-controls.py reproduces this Windows development probe from the
exact commit. It refuses to overwrite its output directory.

The intermediate d3d649fb report is retained too: completed output matches
exactly, but six repeated-context initializations fail. Captured native causes
identify insufficient memory. Dropping GPU wrappers alone still failed after
54 contexts in a follow-up diagnostic. Explicit collection at session cleanup
completed 80 diagnostic cycles. The final 60-observation run includes that
cleanup in the adapter. Native driver memory does not advance Python's garbage
collection thresholds, so deferred device/queue wrapper cycles must be retired
at the session boundary.

**Decision: control quality now passes; production adoption remains unqualified.**
These are prepared synthetic controls on Windows, not the complete STL/3MF
corpus, Job latency, browser performance, Linux/Docker verification or physical
failure containment. Render/encode timings exclude cleanup and were collected
during other development work; they cannot qualify speedup. The historical
969b2966 rejection remains valid for that historical candidate.

## Expanded controls and fresh workers (d1679b86)

The immutable candidate d1679b86b08514e4bb74ec39652ff1d42efc9cb1 was tested on
Windows RTX 5060/Vulkan with 30 observations for each of four additional controls.
The [raw report](windows-corpus-d1679b86.json) retains all 120 completed observations.
ASCII cube, binary cube and duplicate-face controls are exact (90/90). Intersecting
solids fail all 30 observations: two foreground-mask differences and maximum RGBA
difference 148. Output is stable. This is a **quality rejection** for that workload;
the original tolerances remain unchanged.

A supersampled diagnostic finds five differing raw pixels, including two coverage
differences, before image resizing. Thus canonical CPU shading alone cannot
correct GPU coverage/depth selection. This failure is distinct from the previously
fixed interpolation precision problem.

Reproduce with [the Windows probe](windows-corpus-controls.py). The archived replay\nadds a nonzero exit for a failed quality report; measured pixels are unchanged.\nGenerate its four
sources with scripts.viewer_representation_corpus.write_sources into the probe's
campaign-controls-d1679b86 directory, using names ascii-cube, binary-cube,
overlapping-faces and intersecting-solids. The manifest records original file
hashes. The isolated environment additionally has networkx 3.6.1 and lxml 6.1.3
from the repository lock for 3MF loading. An initial setup attempt lacked networkx
and failed before measurement; installing these dependencies preceded the frozen run.
The 3MF probe expands scenes through trimesh, so it does not qualify production
retained-instance loading.

The [WSL fresh-process report](wsl-fresh-process-d1679b86.json) retains 30 attempts
per mode: 30 successful CPU workers, 31 GPU worker resource-limit failures and
29 GPU worker failures without a completed reply. Every supervised execution has
its own execution identifier; successful replies also record the child PID.
No failure is counted as acceleration. Use the committed pilot with --processes 30
--trials 1, candidate wgpu, 64x48 preview, 1024 MiB admission and 15-second deadline.
The binary cube source hash is frozen. Reports intentionally retain failures even
when no native phase measurements could be returned.

Both probes ran alongside other development validation. Windows timing excludes
process supervision and cleanup; WSL cold/reused supervisor time includes the
paired CPU reference. Neither report demonstrates the complete-flow speed gate.
Production integration remains gated; these reports are not Linux/Docker or Intel
hardware qualification.

[Additional WSL/Docker NVIDIA diagnostic](wsl-dzn.md): an isolated non-conformant\nDozen build works only with an explicit experimental override. Default refusal\nand the missing-driver-store failure are retained. This is not deployment qualification.\n