# GPU and compute settings

The unified container image includes the GPU runtime. CPU processing remains
available without a GPU. Passing a device into the container makes it visible;
Settings → GPU reports whether each operation can use it and why it may stay
on the CPU. GPU discovery does not enable AI features or download models.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| VAULT_COMPUTE_MODE | auto | auto or cpu |
| VAULT_COMPUTE_BACKEND | auto | auto, vulkan or opengl; auto selects GL on WSL, otherwise runtime defaults |
| VAULT_COMPUTE_RENDER_POLICY | qualified | qualified requires measured receipts; preview enables bounded render evaluation for human review |
| VAULT_COMPUTE_ADAPTER | unset | Exact physical adapter name or diagnostic identity |
| VAULT_COMPUTE_MEMORY_MB | 1024 | Shared estimated device ceiling, 256–65536 MiB |
| VAULT_MESH_MEMORY_BUDGET_FRACTION | 0.5 | Share of the host or container RAM native mesh work may reserve, 0–1; this is the *system* budget that thumbnails and geometry draw on, apart from the GPU's |
| VAULT_MESH_3MF_MAX_XML_MB | 256 | Uncompressed model XML a 3MF may hold, 1–512 MB. Slicer exports write every coordinate as text, so a 20 MB 3MF can hold over 100 MB of XML. Raise the memory fraction above with it: the triangle ceiling is the memory budget divided by the per-triangle cost (about 1.15 M triangles at 4 GB) |
| VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION | 0.25 | Share of RAM resident AI models may hold, above 0 and below 1 |
| VAULT_COMPUTE_BATCH_WAIT_MS | 10 | Background accumulation ceiling, 0–10 ms |


The memory setting limits PrintStash's estimated GPU use; it cannot reserve
memory against other applications. Stop all API and worker processes before
changing native memory policies. In split deployments, API and workers must
share the same local data root and service user on one host.

Background mesh thumbnail generation uses scratch space under
`VAULT_DATA_ROOT/runtime/prepared/sources/`. PrintStash checks free space on
that volume before rendering and removes the scratch files when the work
finishes. Published thumbnails use the configured thumbnail storage.

## Linux, Docker and WSL

Source install:

~~~sh
cd backend
uv sync --frozen --extra full --extra gpu-render
~~~

Handing a GPU to the container (the same image in every case):

| Host | Compose / `docker run` |
|---|---|
| NVIDIA | `--gpus all -e NVIDIA_DRIVER_CAPABILITIES=all` (Compose: `deploy.resources.reservations.devices` with `driver: nvidia`, `capabilities: [gpu, graphics, compute, utility]`) |
| Intel / AMD (Linux) | `--device /dev/dri`; the entrypoint adds the group that owns each `/dev/dri` node, so no `--group-add` is needed |
| WSL2 | `--device /dev/dxg -v /usr/lib/wsl:/usr/lib/wsl:ro`; the entrypoint sets the Mesa D3D12 variables itself |

Handing in a device only makes it visible. Placement still follows the render policy: with the default `qualified` policy an operation stays on the CPU until a qualification receipt matches, and `VAULT_COMPUTE_RENDER_POLICY=preview` lets an operator evaluate it. Settings → GPU shows what the server found and why each operation is or is not placed on it.

Linux AMD/Intel deployments need a hardware Vulkan driver and access to the appropriate /dev/dri/renderD* node through the service UID's groups. The entrypoint runs the service in the group that owns each mapped /dev/dri node and in any group granted with `--group-add`, never in group 0. A template that cannot leave a device optional, such as Unraid's, maps /dev/dri as a path instead and grants access with `--device-cgroup-rule='c 226:* rmw'`. NVIDIA containers need the supported [NVIDIA Container Toolkit configuration](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/docker-specialized.html), including graphics capabilities for Vulkan. A successful nvidia-smi command proves neither Vulkan nor native WebGPU readiness.

In WSL, the application remains in Linux containers and uses the host bridge:

~~~sh
docker run --rm --device /dev/dxg \
  --mount type=bind,src=/usr/lib/wsl,dst=/usr/lib/wsl,readonly \
  -e VAULT_COMPUTE_RENDER_POLICY=preview \
  -v printstash-gpu-review:/data -p 3000:3000 ghcr.io/xiao-villamor/printstash:latest
~~~

This example creates a separate evaluation vault. Add the same device/library
exposure to the broker-owning service in a split deployment. `auto` selects
wgpu's GL backend when `/dev/dxg` exists. Mesa translates GL to D3D12; no NVIDIA
API, Windows helper, patched Dozen driver or relaxed adapter flag is used.
OpenGL is a best-effort wgpu backend, so test the actual deployment.

## Qualification and fallback

The default `qualified` rendering policy keeps work on the CPU unless a
matching hardware and runtime qualification is available.
`VAULT_COMPUTE_RENDER_POLICY=preview` allows an operator to evaluate rendering;
it does not certify speed or visual quality. Review previews before relying on
that policy. Device failure or unsupported work falls back to CPU processing
within the configured resource limits.

To disable optional acceleration, set `VAULT_COMPUTE_MODE=cpu`.
Browser 3D rendering has a separate [GPU preference](webgpu-preview.md).
