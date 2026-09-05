#!/bin/sh
# Official converter, immutable source plus verified dependency archives.
set -eu
revision=d4da9073616d70a43c151e8c1d7fbff879d2e08a
checksum=d26778992d44b7cfaab99b542d871355f7a66210a512a5503a5b8a941a7d409b
build_root="${1:-/tmp/libbgcode-build}"
prefix="${2:-/opt/libbgcode}"
mkdir -p "$build_root/source"
curl -fsSL "https://github.com/prusa3d/libbgcode/archive/$revision.tar.gz" -o "$build_root/source.tar.gz"
echo "$checksum  $build_root/source.tar.gz" | sha256sum -c -
tar -xzf "$build_root/source.tar.gz" -C "$build_root/source" --strip-components=1
# Upstream pins heatshrink 0.4.1 with SHA256 in deps/heatshrink/heatshrink.cmake.
cmake -S "$build_root/source/deps" -B "$build_root/deps" \
  -DLibBGCode_Deps_SELECT_ALL=OFF -DLibBGCode_Deps_BUILD_heatshrink=ON \
  -DLibBGCode_Deps_DEP_INSTALL_PREFIX="$build_root/dependencies"
cmake --build "$build_root/deps" --parallel 2
cmake -S "$build_root/source" -B "$build_root/build" \
  -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF -DBoost_USE_STATIC_LIBS=ON \
  -DLibBGCode_BUILD_TESTS=OFF -DLibBGCode_BUILD_CMD_TOOL=ON \
  -DCMAKE_PREFIX_PATH="$build_root/dependencies" -DCMAKE_INSTALL_PREFIX="$prefix"
cmake --build "$build_root/build" --parallel 2
cmake --install "$build_root/build"
mkdir -p "$prefix/share/licenses/libbgcode"
cp "$build_root/source/LICENSE" "$prefix/share/licenses/libbgcode/LICENSE"
cp "$build_root/deps/dep_heatshrink-prefix/src/dep_heatshrink/LICENSE" "$prefix/share/licenses/libbgcode/heatshrink-LICENSE"
cp /usr/share/doc/libboost-dev/copyright "$prefix/share/licenses/libbgcode/boost-copyright"
