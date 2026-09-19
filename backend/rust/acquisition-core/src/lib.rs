//! Bounded, DNS-pinned HTTP acquisition with atomic create-only publication.
#![forbid(unsafe_code)]

use std::fmt;
use std::net::{IpAddr, SocketAddr};
use std::path::PathBuf;
use std::sync::Once;
use std::time::Duration;

use reqwest::header::{ACCEPT_ENCODING, CONTENT_DISPOSITION, CONTENT_LENGTH, LOCATION};
use reqwest::{Client, Url};
use sha2::{Digest, Sha256};
use tempfile::NamedTempFile;
use tokio::io::AsyncWriteExt;

static TLS_PROVIDER: Once = Once::new();

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    DestinationExists,
    DownloadFailed,
    DownloadHttpStatus,
    DownloadInvalidDestination,
    DownloadInvalidRequest,
    DownloadNotPublishable,
    DownloadStagingFailed,
    DownloadTargetMismatch,
    DownloadTimeout,
    DownloadTooLarge,
    UrlInvalid,
}

impl Error {
    pub const fn code(self) -> &'static str {
        match self {
            Self::DestinationExists => "download_destination_exists",
            Self::DownloadFailed => "download_failed",
            Self::DownloadHttpStatus => "download_http_status",
            Self::DownloadInvalidDestination => "download_invalid_destination",
            Self::DownloadInvalidRequest => "download_invalid_request",
            Self::DownloadNotPublishable => "download_not_publishable",
            Self::DownloadStagingFailed => "download_staging_failed",
            Self::DownloadTargetMismatch => "download_target_mismatch",
            Self::DownloadTimeout => "download_timeout",
            Self::DownloadTooLarge => "download_too_large",
            Self::UrlInvalid => "url_invalid",
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for Error {}

#[derive(Clone, Debug)]
pub struct Target {
    url: Url,
    host: String,
    ip: IpAddr,
    port: u16,
}

impl Target {
    pub fn try_new(url: String, host: String, ip: String, port: u16) -> Result<Self> {
        let parsed = Url::parse(&url).map_err(|_| Error::UrlInvalid)?;
        let parsed_host = parsed.host_str().ok_or(Error::DownloadTargetMismatch)?;
        if !matches!(parsed.scheme(), "http" | "https")
            || !hosts_match(parsed_host, &host)
            || parsed.port_or_known_default() != Some(port)
            || !parsed.username().is_empty()
            || parsed.password().is_some()
        {
            return Err(Error::DownloadTargetMismatch);
        }
        let ip = ip.parse().map_err(|_| Error::DownloadTargetMismatch)?;
        Ok(Self {
            url: parsed,
            host,
            ip,
            port,
        })
    }
}

#[derive(Clone, Debug)]
pub struct DownloadRequest {
    target: Target,
    directory: PathBuf,
    byte_limit: u64,
    timeout: Duration,
}

impl DownloadRequest {
    pub fn try_new(
        target: Target,
        directory: PathBuf,
        byte_limit: u64,
        timeout_seconds: f64,
    ) -> Result<Self> {
        if directory.as_os_str().is_empty()
            || byte_limit == 0
            || !timeout_seconds.is_finite()
            || !(1.0..=300.0).contains(&timeout_seconds)
        {
            return Err(Error::DownloadInvalidRequest);
        }
        Ok(Self {
            target,
            directory,
            byte_limit,
            timeout: Duration::from_secs_f64(timeout_seconds),
        })
    }
}

struct PendingFile {
    file: Option<NamedTempFile>,
}

impl PendingFile {
    async fn create(directory: PathBuf) -> Result<Self> {
        let file = tokio::task::spawn_blocking(move || NamedTempFile::new_in(directory))
            .await
            .map_err(|_| Error::DownloadStagingFailed)?
            .map_err(|_| Error::DownloadStagingFailed)?;
        Ok(Self { file: Some(file) })
    }

    fn writer(&self) -> Result<std::fs::File> {
        self.file
            .as_ref()
            .ok_or(Error::DownloadStagingFailed)?
            .as_file()
            .try_clone()
            .map_err(|_| Error::DownloadStagingFailed)
    }

    fn publish(mut self, destination: PathBuf) -> Result<()> {
        let file = self.file.take().ok_or(Error::DownloadStagingFailed)?;
        file.persist_noclobber(destination).map_err(|error| {
            if error.error.kind() == std::io::ErrorKind::AlreadyExists {
                Error::DestinationExists
            } else {
                Error::DownloadStagingFailed
            }
        })?;
        Ok(())
    }
}

/// A completed one-hop request whose body stays private until publication.
pub struct Download {
    status: u16,
    location: Option<String>,
    content_disposition: Option<String>,
    written: u64,
    digest: Option<String>,
    directory: PathBuf,
    pending: Option<PendingFile>,
}

impl Download {
    pub const fn status(&self) -> u16 {
        self.status
    }

    pub fn location(&self) -> Option<&str> {
        self.location.as_deref()
    }

    pub fn content_disposition(&self) -> Option<&str> {
        self.content_disposition.as_deref()
    }

    pub const fn written(&self) -> u64 {
        self.written
    }

    pub fn digest(&self) -> Option<&str> {
        self.digest.as_deref()
    }

    pub fn publish(&mut self, destination: PathBuf) -> Result<String> {
        if destination.file_name().is_none() || destination.parent() != Some(&self.directory) {
            return Err(Error::DownloadInvalidDestination);
        }
        let pending = self.pending.take().ok_or(Error::DownloadNotPublishable)?;
        pending.publish(destination)?;
        self.digest.clone().ok_or(Error::DownloadStagingFailed)
    }
}

fn install_tls_provider() {
    TLS_PROVIDER.call_once(|| {
        let _ = rustls::crypto::ring::default_provider().install_default();
    });
}

fn ip_literal(host: &str) -> Option<IpAddr> {
    host.strip_prefix('[')
        .and_then(|value| value.strip_suffix(']'))
        .unwrap_or(host)
        .parse()
        .ok()
}

fn hosts_match(parsed: &str, expected: &str) -> bool {
    match (ip_literal(parsed), ip_literal(expected)) {
        (Some(left), Some(right)) => left == right,
        (None, None) => parsed.eq_ignore_ascii_case(expected),
        _ => false,
    }
}

pub async fn download(request: DownloadRequest) -> Result<Download> {
    install_tls_provider();
    let target = &request.target;
    let client = Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .connect_timeout(request.timeout)
        .read_timeout(request.timeout)
        .resolve(&target.host, SocketAddr::new(target.ip, target.port))
        .build()
        .map_err(|_| Error::DownloadFailed)?;
    let mut response = client
        .get(target.url.clone())
        .header(ACCEPT_ENCODING, "identity")
        .send()
        .await
        .map_err(|error| {
            if error.is_timeout() {
                Error::DownloadTimeout
            } else {
                Error::DownloadFailed
            }
        })?;
    let status = response.status();
    let location = response
        .headers()
        .get(LOCATION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    let content_disposition = response
        .headers()
        .get(CONTENT_DISPOSITION)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned);
    if status.is_redirection() {
        return Ok(Download {
            status: status.as_u16(),
            location,
            content_disposition,
            written: 0,
            digest: None,
            directory: request.directory,
            pending: None,
        });
    }
    if !status.is_success() {
        return Err(Error::DownloadHttpStatus);
    }
    if response
        .headers()
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok())
        .is_some_and(|length| length > request.byte_limit)
    {
        return Err(Error::DownloadTooLarge);
    }
    let pending = PendingFile::create(request.directory.clone()).await?;
    let mut output = tokio::fs::File::from_std(pending.writer()?);
    let mut written = 0u64;
    let mut digest = Sha256::new();
    while let Some(chunk) = response.chunk().await.map_err(|error| {
        if error.is_timeout() {
            Error::DownloadTimeout
        } else {
            Error::DownloadFailed
        }
    })? {
        written = written
            .checked_add(chunk.len() as u64)
            .ok_or(Error::DownloadTooLarge)?;
        if written > request.byte_limit {
            return Err(Error::DownloadTooLarge);
        }
        output
            .write_all(&chunk)
            .await
            .map_err(|_| Error::DownloadStagingFailed)?;
        digest.update(&chunk);
    }
    output
        .flush()
        .await
        .map_err(|_| Error::DownloadStagingFailed)?;
    output
        .sync_all()
        .await
        .map_err(|_| Error::DownloadStagingFailed)?;
    drop(output);
    let digest = digest.finalize();
    let mut digest_hex = String::with_capacity(digest.len() * 2);
    for byte in digest {
        use fmt::Write as _;
        write!(&mut digest_hex, "{byte:02x}").map_err(|_| Error::DownloadStagingFailed)?;
    }
    Ok(Download {
        status: status.as_u16(),
        location,
        content_disposition,
        written,
        digest: Some(digest_hex),
        directory: request.directory,
        pending: Some(pending),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write as _;

    fn request(url: &str, host: &str, ip: &str, port: u16) -> Result<DownloadRequest> {
        DownloadRequest::try_new(
            Target::try_new(url.to_owned(), host.to_owned(), ip.to_owned(), port)?,
            PathBuf::from("/tmp"),
            1024,
            60.0,
        )
    }

    #[test]
    fn rejects_a_target_that_does_not_match_the_url() {
        assert_eq!(
            request(
                "https://example.com/model.stl",
                "other.example",
                "192.0.2.1",
                443,
            )
            .unwrap_err(),
            Error::DownloadTargetMismatch
        );
    }

    #[test]
    fn accepts_equivalent_ipv6_literal_forms() {
        assert!(request(
            "https://[2001:db8::1]:8443/model.stl",
            "2001:db8::1",
            "2001:db8::1",
            8443,
        )
        .is_ok());
    }

    #[test]
    fn rejects_credentials_and_invalid_limits() {
        assert_eq!(
            request(
                "https://user@example.com/model.stl",
                "example.com",
                "192.0.2.1",
                443,
            )
            .unwrap_err(),
            Error::DownloadTargetMismatch
        );
        let target = Target::try_new(
            "https://example.com/model.stl".to_owned(),
            "example.com".to_owned(),
            "192.0.2.1".to_owned(),
            443,
        )
        .unwrap();
        assert_eq!(
            DownloadRequest::try_new(target, PathBuf::from("/tmp"), 0, 60.0).unwrap_err(),
            Error::DownloadInvalidRequest
        );
    }

    #[test]
    fn recognizes_redirect_statuses() {
        for status in [
            reqwest::StatusCode::MOVED_PERMANENTLY,
            reqwest::StatusCode::FOUND,
            reqwest::StatusCode::SEE_OTHER,
            reqwest::StatusCode::TEMPORARY_REDIRECT,
            reqwest::StatusCode::PERMANENT_REDIRECT,
        ] {
            assert!(status.is_redirection());
        }
    }

    #[test]
    fn create_only_publication_preserves_an_existing_destination() {
        let directory = tempfile::tempdir().unwrap();
        let runtime = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();
        let pending = runtime
            .block_on(PendingFile::create(directory.path().to_path_buf()))
            .unwrap();
        pending.writer().unwrap().write_all(b"new bytes").unwrap();
        let destination = directory.path().join("model.stl");
        std::fs::write(&destination, b"existing bytes").unwrap();

        assert_eq!(
            pending.publish(destination.clone()),
            Err(Error::DestinationExists)
        );
        assert_eq!(std::fs::read(destination).unwrap(), b"existing bytes");
    }
}
