"""Defends remote archive transfer stops at cap at the services three mf preview unit boundary.

A regression could select the wrong plate or read beyond the remote archive budget.
"""

from __future__ import annotations

from ._three_mf_preview_shared import (
    EmbeddedGcodeError,
    Event,
    Iterator,
    Path,
    Thread,
    _archive,
    extract_embedded_gcode,
    pytest,
    read_embedded_gcode,
    settings,
    zipfile,
    zlib,
)


def test_remote_archive_transfer_stops_at_cap() -> None:
    calls = 0

    class RemoteBackend:
        def stat_size(self, _key: str) -> int:
            return 1

        def direct_path(self, _key: str) -> None:
            return None

        def stream_chunks(self, _key: str, chunk_size: int) -> Iterator[bytes]:
            nonlocal calls
            calls += 1
            yield b"x" * 11
            calls += 1
            yield b"must not be requested"

    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(
            RemoteBackend(),  # type: ignore[arg-type]
            "remote.3mf",
            max_archive_bytes=10,  # type: ignore[arg-type]
        )

    assert failure.value.code == "embedded_gcode_archive_too_large"
    assert calls == 1


def test_many_zip_entries_are_rejected_before_selection(tmp_path: Path) -> None:
    entries = {f"Metadata/extra_{index}.txt": b"" for index in range(5)}
    entries["Metadata/plate_1.gcode"] = b"G28\n"
    path = _archive(tmp_path / "many.3mf", entries)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_entries=5)

    assert failure.value.code == "embedded_gcode_too_many_entries"


def test_central_directory_size_is_bounded_before_selection(tmp_path: Path) -> None:
    path = _archive(tmp_path / "central.3mf", {"Metadata/plate_1.gcode": b"G28\n"})

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path, max_central_directory_bytes=1)

    assert failure.value.code == "embedded_gcode_central_directory_too_large"


@pytest.mark.parametrize(
    "fault",
    [EOFError("truncated"), NotImplementedError("compression"), zlib.error("crc")],
)
def test_zip_read_faults_have_stable_malformed_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: Exception
) -> None:
    path = _archive(tmp_path / "fault.3mf", {"Metadata/plate_1.gcode": b"G28\n"})

    def fail_open(_archive: zipfile.ZipFile, *_args, **_kwargs):
        raise fault

    monkeypatch.setattr(zipfile.ZipFile, "open", fail_open)

    with pytest.raises(EmbeddedGcodeError) as failure:
        extract_embedded_gcode(path)

    assert failure.value.code == "embedded_gcode_malformed"


def test_preview_capacity_fails_fast_before_second_inflate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _archive(tmp_path / "busy.3mf", {"Metadata/plate_1.gcode": b"G28\n"})
    monkeypatch.setattr(settings._frozen, "three_mf_preview_max_concurrent", 1)
    entered = Event()
    release = Event()
    original = extract_embedded_gcode

    def blocking_extract(*args, **kwargs):
        entered.set()
        assert release.wait(2)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        "app.services.three_mf_preview.extract_embedded_gcode", blocking_extract
    )

    class LocalBackend:
        def stat_size(self, _key: str) -> int:
            return path.stat().st_size

        def direct_path(self, _key: str) -> Path:
            return path

    worker = Thread(
        target=read_embedded_gcode,
        args=(LocalBackend(), "busy.3mf"),  # type: ignore[arg-type]
    )
    worker.start()
    assert entered.wait(2)
    with pytest.raises(EmbeddedGcodeError) as failure:
        read_embedded_gcode(LocalBackend(), "busy.3mf")  # type: ignore[arg-type]
    assert failure.value.code == "embedded_gcode_busy"
    release.set()
    worker.join(timeout=2)
    assert not worker.is_alive()
