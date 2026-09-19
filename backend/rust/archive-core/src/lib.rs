//! Framework-neutral ZIP inspection and bounded extraction.
#![forbid(unsafe_code)]

use std::collections::{HashMap, HashSet};
use std::fmt;
use std::fs::File;
use std::io::{self, BufReader, BufWriter, Read, Write};
use std::path::Path;
use unicode_casefold::UnicodeCaseFold;
use unicode_normalization::UnicodeNormalization;
use zip::ZipArchive;

const COPY_BUFFER_BYTES: usize = 64 * 1024;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ArchiveLimits {
    pub max_entries: usize,
    pub max_entry_bytes: u64,
    pub max_total_bytes: u64,
    pub max_central_directory_bytes: u64,
    pub max_path_bytes: usize,
    pub max_depth: usize,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArchiveEntry {
    pub index: usize,
    pub crc32: u32,
    pub name: String,
    pub size_bytes: u64,
    pub file_type: Option<String>,
    pub is_image: bool,
}

impl ArchiveEntry {
    pub fn entry_id(&self) -> String {
        format!("{}:{:08x}:{}", self.index, self.crc32, self.size_bytes)
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SelectedEntry {
    pub index: usize,
    pub suffix: String,
    pub name: String,
}

#[derive(Debug)]
pub enum ArchiveError {
    Policy(&'static str),
    Io(io::Error),
}

impl ArchiveError {
    pub fn code(&self) -> Option<&'static str> {
        match self {
            Self::Policy(code) => Some(code),
            Self::Io(_) => None,
        }
    }
}

impl fmt::Display for ArchiveError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Policy(code) => formatter.write_str(code),
            Self::Io(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for ArchiveError {}

impl From<io::Error> for ArchiveError {
    fn from(error: io::Error) -> Self {
        Self::Io(error)
    }
}

fn invalid<T>() -> Result<T, ArchiveError> {
    Err(ArchiveError::Policy("archive_invalid"))
}

fn open_archive(path: &Path) -> Result<ZipArchive<BufReader<File>>, ArchiveError> {
    let file = File::open(path)?;
    ZipArchive::new(BufReader::with_capacity(COPY_BUFFER_BYTES, file))
        .map_err(|_| ArchiveError::Policy("archive_invalid"))
}

pub fn safe_entry_name(name: &str) -> bool {
    if name.is_empty() || name.ends_with(['/', '\\']) || name.starts_with(['/', '\\']) {
        return false;
    }
    let bytes = name.as_bytes();
    if bytes.len() > 2 && bytes.get(1) == Some(&b':') {
        return false;
    }
    !name.split(['/', '\\']).any(|component| component == "..")
}

pub fn safe_subdir(name: &str) -> String {
    let normalized = name.replace('\\', "/");
    normalized
        .rsplit_once('/')
        .map_or_else(String::new, |(parent, _)| parent.to_owned())
}

fn suffix(name: &str) -> String {
    let leaf = name.rsplit(['/', '\\']).next().unwrap_or(name);
    leaf.rfind('.')
        .filter(|index| *index > 0)
        .map_or_else(String::new, |index| leaf[index..].to_lowercase())
}

fn is_symlink(mode: Option<u32>) -> bool {
    mode.is_some_and(|value| value & 0o170000 == 0o120000)
}

fn validate_name(name: &str, is_dir: bool, mode: Option<u32>) -> Result<String, ArchiveError> {
    if is_symlink(mode) || (!is_dir && !safe_entry_name(name)) {
        return Err(ArchiveError::Policy("archive_unsafe_entry"));
    }
    Ok(name.replace('\\', "/"))
}

pub fn inspect_archive(
    path: &Path,
    limits: ArchiveLimits,
    file_types: &HashMap<String, String>,
    image_suffixes: &HashSet<String>,
) -> Result<Vec<ArchiveEntry>, ArchiveError> {
    let archive_size = path.metadata()?.len();
    let mut archive = open_archive(path)?;
    if archive.len() > limits.max_entries {
        return Err(ArchiveError::Policy("archive_too_many_entries"));
    }
    let central_size = archive_size.saturating_sub(archive.central_directory_start());
    if central_size > limits.max_central_directory_bytes {
        return Err(ArchiveError::Policy("archive_too_large"));
    }

    let mut entries = Vec::new();
    let mut normalized_names = HashSet::with_capacity(archive.len());
    let mut total = 0u64;
    for index in 0..archive.len() {
        let file = archive
            .by_index(index)
            .map_err(|_| ArchiveError::Policy("archive_invalid"))?;
        let original_name = file.name().to_owned();
        let normalized = original_name.replace('\\', "/").nfc().collect::<String>();
        if normalized.len() > limits.max_path_bytes
            || normalized.split('/').count() > limits.max_depth.saturating_add(1)
        {
            return Err(ArchiveError::Policy("archive_path_too_deep"));
        }
        let folded = normalized.as_str().case_fold().collect::<String>();
        if !normalized_names.insert(folded) {
            return Err(ArchiveError::Policy("archive_duplicate_entry"));
        }
        validate_name(&original_name, file.is_dir(), file.unix_mode())?;
        if file.is_dir() {
            continue;
        }
        if file.size() > limits.max_entry_bytes {
            return Err(ArchiveError::Policy("archive_entry_too_large"));
        }
        total = total
            .checked_add(file.size())
            .ok_or(ArchiveError::Policy("archive_too_large"))?;
        if total > limits.max_total_bytes {
            return Err(ArchiveError::Policy("archive_too_large"));
        }
        let extension = suffix(&original_name);
        let file_type = file_types.get(&extension).cloned();
        let is_image = image_suffixes.contains(&extension);
        if file_type.is_none() && !is_image {
            continue;
        }
        entries.push(ArchiveEntry {
            index,
            crc32: file.crc32(),
            name: original_name,
            size_bytes: file.size(),
            file_type,
            is_image,
        });
    }
    Ok(entries)
}

pub struct ArchiveReader {
    archive: ZipArchive<BufReader<File>>,
}

impl ArchiveReader {
    pub fn open(path: &Path) -> Result<Self, ArchiveError> {
        Ok(Self {
            archive: open_archive(path)?,
        })
    }

    pub fn selected_entries(
        &mut self,
        names: &[String],
        max_entry_bytes: u64,
        importable_suffixes: &HashSet<String>,
    ) -> Result<Vec<SelectedEntry>, ArchiveError> {
        let wanted: HashSet<&str> = names.iter().map(String::as_str).collect();
        let mut selected = Vec::new();
        for index in 0..self.archive.len() {
            let file = self
                .archive
                .by_index(index)
                .map_err(|_| ArchiveError::Policy("archive_invalid"))?;
            if !wanted.contains(file.name()) || file.is_dir() {
                continue;
            }
            let normalized = validate_name(file.name(), false, file.unix_mode())?;
            if file.size() > max_entry_bytes {
                return Err(ArchiveError::Policy("archive_entry_too_large"));
            }
            let extension = suffix(file.name());
            if importable_suffixes.contains(&extension) {
                selected.push(SelectedEntry {
                    index,
                    suffix: extension,
                    name: normalized,
                });
            }
        }
        Ok(selected)
    }

    pub fn selected_entry(
        &mut self,
        name: &str,
        max_entry_bytes: u64,
        importable_suffixes: &HashSet<String>,
    ) -> Result<Option<SelectedEntry>, ArchiveError> {
        for index in 0..self.archive.len() {
            let file = self
                .archive
                .by_index(index)
                .map_err(|_| ArchiveError::Policy("archive_invalid"))?;
            if file.name() != name {
                continue;
            }
            if file.is_dir() {
                return Ok(None);
            }
            let normalized = validate_name(file.name(), false, file.unix_mode())?;
            if file.size() > max_entry_bytes {
                return Err(ArchiveError::Policy("archive_entry_too_large"));
            }
            let extension = suffix(file.name());
            return Ok(importable_suffixes
                .contains(&extension)
                .then_some(SelectedEntry {
                    index,
                    suffix: extension,
                    name: normalized,
                }));
        }
        invalid()
    }

    pub fn extract_to(
        &mut self,
        index: usize,
        destination: &Path,
        max_entry_bytes: u64,
    ) -> Result<(), ArchiveError> {
        let mut source = self
            .archive
            .by_index(index)
            .map_err(|_| ArchiveError::Policy("archive_invalid"))?;
        validate_name(source.name(), source.is_dir(), source.unix_mode())?;
        if source.is_dir() {
            return Err(ArchiveError::Policy("archive_unsafe_entry"));
        }
        if source.size() > max_entry_bytes {
            return Err(ArchiveError::Policy("archive_entry_too_large"));
        }
        let parent = destination.parent().ok_or_else(|| {
            ArchiveError::Io(io::Error::new(
                io::ErrorKind::InvalidInput,
                "staging destination has no parent",
            ))
        })?;
        std::fs::create_dir_all(parent)?;
        let temporary = tempfile::NamedTempFile::new_in(parent)?;
        let mut output = BufWriter::with_capacity(COPY_BUFFER_BYTES, temporary);
        let mut copied = 0u64;
        let mut buffer = [0u8; COPY_BUFFER_BYTES];
        loop {
            let allowed = max_entry_bytes.saturating_sub(copied).saturating_add(1);
            let requested = buffer
                .len()
                .min(usize::try_from(allowed).unwrap_or(usize::MAX));
            let count = source
                .read(&mut buffer[..requested])
                .map_err(|_| ArchiveError::Policy("archive_invalid"))?;
            if count == 0 {
                break;
            }
            copied += count as u64;
            if copied > max_entry_bytes {
                return Err(ArchiveError::Policy("archive_entry_too_large"));
            }
            output.write_all(&buffer[..count])?;
        }
        if copied != source.size() {
            return Err(ArchiveError::Policy("archive_invalid"));
        }
        output.flush()?;
        output.get_ref().as_file().sync_all()?;
        let temporary = output
            .into_inner()
            .map_err(|error| ArchiveError::Io(error.into_error()))?;
        temporary
            .persist_noclobber(destination)
            .map_err(|error| ArchiveError::Io(error.error))?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::{inspect_archive, safe_entry_name, safe_subdir, ArchiveLimits, ArchiveReader};
    use std::collections::{HashMap, HashSet};
    use std::fs::File;
    use std::io::Write;
    use tempfile::tempdir;
    use zip::write::SimpleFileOptions;
    use zip::ZipWriter;

    fn sample_archive(path: &std::path::Path) {
        let mut archive = ZipWriter::new(File::create(path).unwrap());
        archive
            .start_file("parts/model.stl", SimpleFileOptions::default())
            .unwrap();
        archive.write_all(b"solid").unwrap();
        archive.finish().unwrap();
    }

    fn limits() -> ArchiveLimits {
        ArchiveLimits {
            max_entries: 10,
            max_entry_bytes: 100,
            max_total_bytes: 100,
            max_central_directory_bytes: 1_000,
            max_path_bytes: 100,
            max_depth: 4,
        }
    }

    #[test]
    fn rejects_unsafe_names() {
        for name in ["", "dir/", "/root", "C:/root", "../root", "a/../root"] {
            assert!(!safe_entry_name(name), "accepted {name:?}");
        }
    }

    #[test]
    fn accepts_relative_names() {
        for name in [
            "part.stl",
            "nested/part.stl",
            "nested\\part.stl",
            "a..b.stl",
        ] {
            assert!(safe_entry_name(name), "refused {name:?}");
        }
    }

    #[test]
    fn returns_normalized_parent() {
        assert_eq!(safe_subdir("part.stl"), "");
        assert_eq!(safe_subdir("a\\b\\part.stl"), "a/b");
    }

    #[test]
    fn inspects_supported_entries() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("models.zip");
        sample_archive(&path);
        let entries = inspect_archive(
            &path,
            limits(),
            &HashMap::from([(".stl".to_owned(), "stl".to_owned())]),
            &HashSet::new(),
        )
        .unwrap();

        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].name, "parts/model.stl");
        assert_eq!(entries[0].file_type.as_deref(), Some("stl"));
        assert_eq!(entries[0].size_bytes, 5);
    }

    #[test]
    fn extraction_does_not_replace_an_existing_destination() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("models.zip");
        sample_archive(&path);
        let destination = directory.path().join("model.stl");
        std::fs::write(&destination, b"existing").unwrap();
        let mut archive = ArchiveReader::open(&path).unwrap();

        assert!(archive.extract_to(0, &destination, 100).is_err());
        assert_eq!(std::fs::read(destination).unwrap(), b"existing");
    }
}
