//! Safe, bounded adapter around the official Prusa libbgcode implementation.
// CXX generates the FFI unsafe code; this crate exposes only safe Rust functions.

use std::fmt;
use std::path::Path;

// Keep the native zlib archive in the final link. The C++ objects call zlib
// directly, so Rust otherwise sees no symbol use from this dependency.
use libz_sys as _;

#[cxx::bridge(namespace = "printstash::bgcode")]
mod ffi {
    struct Thumbnail {
        format: u16,
        width: u16,
        height: u16,
        data: Vec<u8>,
    }

    struct Inspection {
        metadata_text: String,
        thumbnails: Vec<Thumbnail>,
        saw_block: bool,
        saw_gcode: bool,
        valid: bool,
    }

    unsafe extern "C++" {
        include!("adapter.hpp");

        fn inspect(
            path: &CxxString,
            include_thumbnails: bool,
            validate_all_blocks: bool,
        ) -> Result<Inspection>;
    }
}

#[derive(Debug)]
pub struct Error(cxx::Exception);

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

impl std::error::Error for Error {}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Thumbnail {
    pub format: u16,
    pub width: u16,
    pub height: u16,
    pub data: Vec<u8>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Inspection {
    pub metadata_text: String,
    pub thumbnails: Vec<Thumbnail>,
    pub saw_block: bool,
    pub saw_gcode: bool,
    pub valid: bool,
}

pub fn inspect(
    path: &Path,
    include_thumbnails: bool,
    validate_all_blocks: bool,
) -> Result<Inspection, Error> {
    let path = path.to_string_lossy();
    cxx::let_cxx_string!(native_path = path.as_ref());
    ffi::inspect(&native_path, include_thumbnails, validate_all_blocks)
        .map(|result| Inspection {
            metadata_text: result.metadata_text,
            thumbnails: result
                .thumbnails
                .into_iter()
                .map(|thumbnail| Thumbnail {
                    format: thumbnail.format,
                    width: thumbnail.width,
                    height: thumbnail.height,
                    data: thumbnail.data,
                })
                .collect(),
            saw_block: result.saw_block,
            saw_gcode: result.saw_gcode,
            valid: result.valid,
        })
        .map_err(Error)
}
