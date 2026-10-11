# WSL NVIDIA development diagnostic

An isolated Mesa 26.2.4 Dozen build exposes the physical RTX 5060 through
D3D12 on this WSL installation. Its Vulkan conformance version is 0.0.0.0.
wgpu 0.32.0 correctly hides it by default. The unchanged diagnostic refuses it.
A separate development probe using the public set_instance_extras API with
AllowUnderlyingNoncompliantAdapter completes one bounded render/readback.

This is **not production qualification**. Do not enable the override in application
code, count the result as a supported driver, or use it to bypass acceptance gates.
The driver prints its non-conformance warning and wgpu reports missing downlevel
features. Native Linux conformant-driver and Intel results remain required.

## Evidence

- [Vulkan device identity](wsl-dzn-vulkaninfo.txt).
- [Default refusal](wsl-dzn-doctor.txt).
- [Explicit experimental WSL diagnostic](wsl-dzn-experimental.txt).
- [Container with missing driver-store mount](wsl-dzn-docker.txt): refused.
- [Container with driver-store mount](wsl-dzn-docker-drivers.txt): passed with
  UID/GID 12345:12345 and no privileged mode.

The container uses research image printstash-gpu-check:pri16-a9b38886 with the
d1679b86 scripts mounted read-only; application production rendering remains CPU.
The host driver, system Mesa installation and Docker GPU runtime were unchanged.
Only each disposable diagnostic received device access.

## Reproduction

Build the isolated compiler image using [wsl-dzn.Dockerfile](wsl-dzn.Dockerfile).
The file records the tested build-tool versions, DirectX-Headers v1.619.5 and
the downloaded Mesa archive hash. Distribution package versions remain dependent
on the Ubuntu repository and this is not a supported deployment artifact.

Copy /opt/dzn from a temporary container into /tmp/pri16-dzn-runtime and remove
that temporary container. Change the copied dzn_icd.x86_64.json library_path to
/tmp/pri16-dzn-runtime/lib/x86_64-linux-gnu/libvulkan_dzn.so. Point VK_DRIVER_FILES
at that JSON and LD_LIBRARY_PATH at /usr/lib/wsl/lib for the probe process only.

The experimental Python preamble is:

    from wgpu.backends.wgpu_native.extras import set_instance_extras
    set_instance_extras(
        backends=["Vulkan"],
        flags=["AllowUnderlyingNoncompliantAdapter"],
    )
    from scripts.webgpu_doctor import main
    raise SystemExit(main())

For a disposable container, grant only /dev/dxg and mount /usr/lib/wsl/lib,
/usr/lib/wsl/drivers and the copied driver directory read-only at their existing
absolute paths. The driver-store mount is necessary for D3D12 device creation.
Set the same two environment variables, run as UID/GID 12345:12345, and execute
the preamble using the research image's Python. Do not expose devices to the API.

[Official Mesa build documentation](https://docs.mesa3d.org/meson.html) and
[release archive](https://archive.mesa3d.org/) describe the upstream build inputs.
