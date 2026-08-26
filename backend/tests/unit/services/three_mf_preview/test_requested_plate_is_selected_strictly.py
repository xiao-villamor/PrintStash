"""Defends requested plate is selected strictly at the services three mf preview unit boundary.

A regression could select the wrong plate or read beyond the remote archive budget.
"""

from __future__ import annotations

from ._three_mf_preview_shared import (
    EmbeddedGcodeError,
    FileType,
    Iterator,
    Path,
    SimpleNamespace,
    _archive,
    _archive_bytes,
    extract_embedded_gcode,
    os,
    pytest,
    read_embedded_gcode,
    reproducibility_payload,
    struct,
    zipfile,
)


def test_requested_plate_is_selected_strictly(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "project.3mf",
        {
            "Metadata/plate_1.gcode": b"; plate 1\n",
            "Metadata/plate_2.gcode": b"; plate 2\n",
        },
    )

    result = extract_embedded_gcode(path, plate_index=2)

    assert result.filename == "plate_2.gcode"
    assert result.content == b"; plate 2\n"


def test_single_candidate_is_fallback_without_plate_index(tmp_path: Path) -> None:
    path = _archive(tmp_path / "project.3mf", {"Metadata/plate_7.gcode": b"G28\n"})

    result = extract_embedded_gcode(path)

    assert result.filename == "plate_7.gcode"
    assert result.content == b"G28\n"


@pytest.mark.parametrize(
    ("entries", "code"),
    [
        ({"Metadata/plate_1.gcode": b"G28\n"}, "embedded_gcode_not_found"),
        (
            {
                "Metadata/plate_1.gcode": b"one\n",
                "Metadata/plate_2.gcode": b"two\n",
            },
            "embedded_gcode_ambiguous",
        ),
    ],
)
def test_missing_or_ambiguous_candidates_are_stable(
    tmp_path: Path, entries: dict[str, bytes], code: str
) -> None:
    path = _archive(tmp_path / "project.3mf", entries)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, plate_index=9 if code.endswith("found") else None)

    assert failure.value.code == code


def test_traversal_and_case_variants_are_ignored(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "project.3mf",
        {
            "../Metadata/plate_1.gcode": b"unsafe\n",
            "metadata/plate_1.gcode": b"wrong case\n",
            "Metadata/plate_1.gcode": b"safe\n",
        },
    )

    result = extract_embedded_gcode(path, plate_index=1)

    assert result.content == b"safe\n"


def test_malformed_zip_has_stable_code(tmp_path: Path) -> None:
    path = tmp_path / "broken.3mf"
    path.write_bytes(b"not a zip")

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path)

    assert failure.value.code == "embedded_gcode_malformed"


@pytest.mark.parametrize(
    "limits",
    [
        {"max_uncompressed_bytes": 0},
        {"max_compression_ratio": 0},
        {"max_archive_bytes": 0},
        {"max_entries": 0},
        {"max_central_directory_bytes": 0},
    ],
)
def test_non_positive_preview_limits_are_rejected(
    tmp_path: Path, limits: dict[str, int]
) -> None:
    path = _archive(
        tmp_path / "invalid-limits.3mf", {"Metadata/plate_1.gcode": b"G28\n"}
    )

    with pytest.raises(ValueError, match="embedded_gcode_limits_invalid"):
        extract_embedded_gcode(path, **limits)


def test_negative_plate_index_is_not_a_valid_member_request(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "negative-plate.3mf", {"Metadata/plate_1.gcode": b"G28\n"}
    )

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, plate_index=-1)

    assert failure.value.code == "embedded_gcode_not_found"


def test_archive_cap_and_missing_archive_keep_stable_errors(tmp_path: Path) -> None:
    path = _archive(tmp_path / "too-large.3mf", {"Metadata/plate_1.gcode": b"G28\n"})
    with pytest.raises(EmbeddedGcodeError) as oversized:
        extract_embedded_gcode(path, max_archive_bytes=1)
    assert oversized.value.code == "embedded_gcode_archive_too_large"

    with pytest.raises(FileNotFoundError):
        extract_embedded_gcode(tmp_path / "missing.3mf")


def test_zip_footer_without_eocd_defers_to_zipfile(tmp_path: Path) -> None:
    from app.services import three_mf_preview

    path = tmp_path / "no-footer.3mf"
    path.write_bytes(b"x" * three_mf_preview._ZIP_EOCD_BYTES)

    # The footer scanner is deliberately conservative: an unrecognised tail
    # is left for ZipFile to classify as malformed.
    assert (
        three_mf_preview._zip_footer_limits(
            path,
            path.stat().st_size,
            max_entries=10,
            max_central_directory_bytes=1024,
        )
        is None
    )


def test_zip64_footer_metadata_is_read_before_zipfile(tmp_path: Path) -> None:
    from app.services import three_mf_preview

    record = bytearray(56)
    record[:4] = three_mf_preview._ZIP64_EOCD
    struct.pack_into("<Q", record, 32, 1)
    struct.pack_into("<Q", record, 40, 1)
    struct.pack_into("<Q", record, 48, 100)
    zip64_offset = 20
    locator = struct.pack(
        "<4sLQL", three_mf_preview._ZIP64_EOCD_LOCATOR, 0, zip64_offset, 1
    )
    classic = struct.pack(
        "<4s4H2LH",
        three_mf_preview._ZIP_EOCD,
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path = tmp_path / "zip64-footer.3mf"
    path.write_bytes(b"x" * zip64_offset + bytes(record) + locator + classic)

    assert (
        three_mf_preview._zip_footer_limits(
            path,
            path.stat().st_size,
            max_entries=2,
            max_central_directory_bytes=2,
        )
        is None
    )


def test_central_directory_runtime_size_is_checked_after_footer_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import three_mf_preview

    path = _archive(
        tmp_path / "central-runtime.3mf", {"Metadata/plate_1.gcode": b"G28\n"}
    )
    monkeypatch.setattr(
        three_mf_preview, "_zip_footer_limits", lambda *args, **kwargs: None
    )

    real_zip_file = zipfile.ZipFile

    class SizedZipFile(real_zip_file):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.size_cd = 2

    monkeypatch.setattr(three_mf_preview.zipfile, "ZipFile", SizedZipFile)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_central_directory_bytes=1)
    assert failure.value.code == "embedded_gcode_central_directory_too_large"


def test_runtime_entry_count_is_checked_after_footer_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import three_mf_preview

    path = _archive(
        tmp_path / "entry-runtime.3mf",
        {"Metadata/extra.txt": b"x", "Metadata/plate_1.gcode": b"G28\n"},
    )
    monkeypatch.setattr(
        three_mf_preview, "_zip_footer_limits", lambda *args, **kwargs: None
    )

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_entries=1)
    assert failure.value.code == "embedded_gcode_too_many_entries"


def test_extract_normalizes_unexpected_footer_errors(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services import three_mf_preview

    path = _archive(tmp_path / "footer-error.3mf", {"Metadata/plate_1.gcode": b"G28\n"})
    monkeypatch.setattr(
        three_mf_preview,
        "_zip_footer_limits",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad footer")),
    )

    with pytest.raises(EmbeddedGcodeError, match="embedded_gcode_malformed"):
        extract_embedded_gcode(path)


def test_safe_member_name_rejects_all_noncanonical_variants() -> None:
    from app.services import three_mf_preview

    for name in (
        "",
        "a\\b",
        "/absolute",
        "Metadata/./plate_1.gcode",
        "Metadata//plate_1.gcode",
        "Metadata/../plate_1.gcode",
        "Metadata/plate_1.gcode\x00",
        "Metadata/plate_e\u0301.gcode",
    ):
        assert three_mf_preview._safe_member_name(name) is False


def test_remote_archive_success_closes_stream_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_bytes = _archive_bytes({"Metadata/plate_1.gcode": b"G28\n"})
    closed = False
    temporary = tmp_path / "remote-preview.3mf"

    def fake_mkstemp(*_args, **_kwargs):
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        return fd, str(temporary)

    from app.services import three_mf_preview

    monkeypatch.setattr(three_mf_preview.tempfile, "mkstemp", fake_mkstemp)

    class ChunkStream:
        def __init__(self):
            self._chunks = iter((archive_bytes[:3], archive_bytes[3:]))

        def __iter__(self):
            return self

        def __next__(self):
            return next(self._chunks)

        def close(self):
            nonlocal closed
            closed = True

    class RemoteBackend:
        def stat_size(self, _key: str) -> int:
            return len(archive_bytes)

        def direct_path(self, _key: str) -> None:
            return None

        def stream_chunks(self, _key: str, chunk_size: int):
            del chunk_size
            return ChunkStream()

    result = read_embedded_gcode(RemoteBackend(), "remote.3mf")  # type: ignore[arg-type]
    assert result.content == b"G28\n"
    assert closed is True
    assert temporary.exists() is False


def test_read_remote_missing_blob_preserves_file_not_found() -> None:
    class MissingBackend:
        def stat_size(self, _key: str) -> int:
            raise FileNotFoundError("gone")

    with pytest.raises(FileNotFoundError):
        read_embedded_gcode(MissingBackend(), "missing.3mf")  # type: ignore[arg-type]


def test_remote_archive_error_closes_stream_and_removes_temp_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_bytes = _archive_bytes({"Metadata/plate_1.gcode": b"G28\n"})
    temporary = tmp_path / "remote-error.3mf"
    closed = False

    def fake_mkstemp(*_args, **_kwargs):
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        return fd, str(temporary)

    from app.services import three_mf_preview

    monkeypatch.setattr(three_mf_preview.tempfile, "mkstemp", fake_mkstemp)

    class FailingStream:
        def __iter__(self):
            return self

        def __next__(self):
            if not hasattr(self, "yielded"):
                self.yielded = True
                return archive_bytes[:3]
            raise OSError("remote read failed")

        def close(self):
            nonlocal closed
            closed = True

    class RemoteBackend:
        def stat_size(self, _key: str) -> int:
            return len(archive_bytes)

        def direct_path(self, _key: str) -> None:
            return None

        def stream_chunks(self, _key: str, chunk_size: int):
            del chunk_size
            return FailingStream()

    with pytest.raises(EmbeddedGcodeError, match="embedded_gcode_malformed"):
        read_embedded_gcode(RemoteBackend(), "remote.3mf")  # type: ignore[arg-type]
    assert closed is True
    assert temporary.exists() is False


def test_member_read_that_exceeds_cap_is_rejected_during_streaming(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path / "stream-cap.3mf", {"Metadata/plate_1.gcode": b"G28\n"})
    original_open = zipfile.ZipFile.open

    class OversizedSource:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self, _size: int) -> bytes:
            return b"x" * 5

    def fake_open(_archive, *_args, **_kwargs):
        return OversizedSource()

    monkeypatch.setattr(zipfile.ZipFile, "open", fake_open)
    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_uncompressed_bytes=4)
    assert failure.value.code == "embedded_gcode_too_large"
    monkeypatch.setattr(zipfile.ZipFile, "open", original_open)


def test_remote_backend_errors_are_normalized_and_cleaned_up() -> None:
    class BrokenBackend:
        def stat_size(self, _key: str) -> int:
            raise OSError("remote unavailable")

        def direct_path(self, _key: str) -> None:
            return None

    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(BrokenBackend(), "remote.3mf")  # type: ignore[arg-type]
    assert failure.value.code == "embedded_gcode_malformed"


def test_uncompressed_cap_is_enforced_before_and_during_read(tmp_path: Path) -> None:
    path = _archive(tmp_path / "project.3mf", {"Metadata/plate_1.gcode": b"12345"})

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_uncompressed_bytes=4)

    assert failure.value.code == "embedded_gcode_too_large"


def test_compression_ratio_is_rejected_as_bomb(tmp_path: Path) -> None:
    path = _archive(
        tmp_path / "project.3mf",
        {"Metadata/plate_1.gcode": b"G28\n" * 100},
    )

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_compression_ratio=2)

    assert failure.value.code == "embedded_gcode_bomb"


def test_storage_reads_through_direct_path_without_persisting_copy(
    tmp_path: Path,
) -> None:
    archive_path = _archive(
        tmp_path / "project.3mf", {"Metadata/plate_1.gcode": b"G28\n"}
    )
    calls: list[str] = []

    class Backend:
        def stat_size(self, key: str) -> int:
            calls.append(key)
            return archive_path.stat().st_size

        def direct_path(self, key: str) -> Path:
            calls.append(key)
            return archive_path

    result = read_embedded_gcode(
        Backend(),  # type: ignore[arg-type]
        "opaque-3mf-key",
        plate_index=1,  # type: ignore[arg-type]
    )

    assert result.content == b"G28\n"
    assert calls == ["opaque-3mf-key", "opaque-3mf-key"]
    assert list(tmp_path.glob("*.gcode")) == []


@pytest.mark.parametrize("plate_index", [None, 3, -2])
def test_reproducibility_contract_links_only_archived_3mf(
    plate_index: int | None,
) -> None:
    job = SimpleNamespace(
        artifact_evidence="project_archived",
        source="external",
        file_id=42,
        external_display_name="Benchy",
        external_task_id="task-1",
        external_subtask_id=None,
        external_project_id="project-1",
        external_profile_id=None,
        external_gcode_file="benchy.gcode",
        external_plate_index=plate_index,
        external_current_layer=None,
        external_total_layers=None,
        external_nozzle_diameter=None,
        artifact_capture_error=None,
        artifact_capture_error_code=None,
        artifact_capture_error_message=None,
    )

    payload = reproducibility_payload(
        job,
        file_type=FileType.THREE_MF,
        download_url="/api/v1/files/42/download",
    )

    suffix = (
        f"?plate_index={plate_index}"
        if plate_index is not None and plate_index >= 0
        else ""
    )
    expected = f"/api/v1/files/42/embedded-gcode{suffix}"
    assert payload["toolpath_preview_url"] == expected
    assert payload["reproducibility"]["toolpath_preview_url"] == expected


def test_remote_archive_size_is_checked_before_transfer() -> None:
    calls = 0

    class RemoteBackend:
        def stat_size(self, _key: str) -> int:
            return 11

        def direct_path(self, _key: str) -> None:
            return None

        def stream_chunks(self, _key: str, chunk_size: int) -> Iterator[bytes]:
            nonlocal calls
            calls += 1
            yield b"never downloaded"

    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(
            RemoteBackend(),  # type: ignore[arg-type]
            "remote.3mf",
            max_archive_bytes=10,  # type: ignore[arg-type]
        )

    assert failure.value.code == "embedded_gcode_archive_too_large"
    assert calls == 0
