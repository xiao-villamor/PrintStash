use std::{env, fs, path::PathBuf};

fn main() {
    for path in [
        "src/adapter.cpp",
        "src/adapter.hpp",
        "src/lib.rs",
        "vendor/libbgcode/core/core.cpp",
        "vendor/libbgcode/core/core.hpp",
        "vendor/libbgcode/core/core_impl.hpp",
        "vendor/heatshrink/heatshrink_common.h",
        "vendor/heatshrink/heatshrink_config.h",
        "vendor/heatshrink/heatshrink_decoder.c",
        "vendor/heatshrink/heatshrink_decoder.h",
    ] {
        println!("cargo:rerun-if-changed={path}");
    }
    let output = PathBuf::from(env::var_os("OUT_DIR").expect("Cargo OUT_DIR"));
    fs::create_dir_all(output.join("core")).expect("create core include directory");
    fs::write(
        output.join("core/export.h"),
        "#pragma once\n#define BGCODE_CORE_EXPORT\n",
    )
    .expect("write core export header");
    cc::Build::new()
        .include("vendor")
        .include("vendor/heatshrink")
        .flag_if_supported("-Wno-implicit-fallthrough")
        .file("vendor/heatshrink/heatshrink_decoder.c")
        .compile("printstash_heatshrink");

    let zlib_include = env::var_os("DEP_Z_INCLUDE").expect("libz-sys include metadata");
    cxx_build::bridge("src/lib.rs")
        .std("c++17")
        .define("LibBGCode_VERSION", "\"0.3.0\"")
        .include("src")
        .include("vendor")
        .include("vendor/libbgcode")
        .include(&output)
        .include(zlib_include)
        .file("vendor/libbgcode/core/core.cpp")
        .file("src/adapter.cpp")
        .compile("printstash_libbgcode");
}
