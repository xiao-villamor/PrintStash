# Development-only non-conformant driver probe. Not an application image.
FROM ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends build-essential ninja-build python3-venv bison flex pkg-config libdrm-dev libexpat1-dev libzstd-dev zlib1g-dev libudev-dev curl ca-certificates xz-utils git
RUN python3 -m venv /opt/build-tools && /opt/build-tools/bin/pip install meson==1.12.1 Mako==1.4.3 MarkupSafe==3.0.4 PyYAML==6.0.3 packaging==26.3
ENV PATH=/opt/build-tools/bin:$PATH
WORKDIR /build
RUN git clone --depth 1 --branch v1.619.5 https://github.com/microsoft/DirectX-Headers.git && meson setup DirectX-Headers/build DirectX-Headers --prefix=/opt/dzn -Dbuild-test=false && ninja -C DirectX-Headers/build install
RUN curl --fail --location https://archive.mesa3d.org/mesa-26.2.4.tar.xz --output mesa.tar.xz && echo "bce5f7fbebb934373b86c999a064d52fb5065878dc57f287f95346648ec832e9  mesa.tar.xz" | sha256sum -c - && tar -xf mesa.tar.xz
ENV PKG_CONFIG_PATH=/opt/dzn/lib/x86_64-linux-gnu/pkgconfig:/opt/dzn/lib/pkgconfig
RUN meson setup mesa-build mesa-26.2.4 --prefix=/opt/dzn --buildtype=release -Dvulkan-drivers=microsoft-experimental -Dgallium-drivers=[] -Dplatforms=[] -Dglx=disabled -Degl=disabled -Dgbm=disabled -Dllvm=disabled -Dopengl=false -Dgles1=disabled -Dgles2=disabled -Dvideo-codecs=[] && ninja -C mesa-build -j 2 install
