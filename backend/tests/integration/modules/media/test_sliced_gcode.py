"""A stored sliced Revision must identify one real plate before dispatch."""

from pathlib import Path

import pytest

from app.core.errors import OperationError
from app.db.models import FileType
from app.modules.media.sliced_gcode import plate_toolpath, selected_plate
from app.modules.media.three_mf_preview import EmbeddedGcodeError
from app.modules.storage.storage_backend.runtime import get_backend
from tests.fixtures.three_mf_projects import build_sliced_project


@pytest.fixture
def sliced(make_model, make_file):
    content = build_sliced_project(plate_numbers=(2, 10))
    backend = get_backend()
    key = "sliced/part.gcode.3mf"
    backend.write_bytes(content, key)
    return make_file(
        make_model(),
        filename="part.gcode.3mf",
        file_type=FileType.GCODE,
        path=key,
        size_bytes=len(content),
    )


class TestSelectedPlate:
    def test_selects_a_real_plate(self, sliced):
        assert selected_plate(sliced, 10) == 10

    def test_requires_multiplate_selection(self, sliced):
        with pytest.raises(OperationError, match="plate_selection_required"):
            selected_plate(sliced, None)

    def test_refuses_absent_plate(self, sliced):
        with pytest.raises(OperationError, match="embedded_gcode_not_found"):
            selected_plate(sliced, 1)

    def test_resolves_the_only_plate(self, sliced):
        Path(get_backend().direct_path(sliced.path)).write_bytes(
            build_sliced_project(plate_numbers=(10,))
        )
        assert selected_plate(sliced, None) == 10

    def test_refuses_malformed_archive(self, sliced):
        Path(get_backend().direct_path(sliced.path)).write_bytes(b"invalid archive")
        with pytest.raises(OperationError, match="embedded_gcode_malformed"):
            selected_plate(sliced, 2)

    def test_refuses_unsliced_archive(self, sliced):
        Path(get_backend().direct_path(sliced.path)).write_bytes(
            build_sliced_project(plate_numbers=())
        )
        with pytest.raises(OperationError, match="plate_selection_required"):
            selected_plate(sliced, None)

    def test_refuses_a_missing_archive(self, sliced):
        Path(get_backend().direct_path(sliced.path)).unlink()
        with pytest.raises(OperationError, match="file_blob_unavailable"):
            selected_plate(sliced, 2)

    def test_refuses_plate_selection_on_text(self, make_model, make_file):
        file = make_file(make_model(), file_type=FileType.GCODE)
        with pytest.raises(OperationError, match="plate_not_supported"):
            selected_plate(file, 1)

    def test_text_does_not_need_a_plate(self, make_model, make_file):
        file = make_file(make_model(), file_type=FileType.GCODE)
        assert selected_plate(file, None) is None


class TestPlateToolpath:
    def test_extracts_only_selected_plate(self, sliced):
        with get_backend().local_path(sliced.path) as source:
            with plate_toolpath(source, 10) as path:
                content = path.read_text()
                assert "plate 10\n" in content
                assert "plate 2\n" not in content
                assert path.suffix == ".gcode"
            assert not path.exists()

    def test_cleans_up_after_a_transport_failure(self, sliced):
        paths: list[Path] = []
        with get_backend().local_path(sliced.path) as source:
            with pytest.raises(OSError, match="transport"):
                with plate_toolpath(source, 2) as path:
                    paths.append(path)
                    raise OSError("transport")
        assert not paths[0].exists()

    def test_obeys_decompression_limit(self, sliced, monkeypatch):
        from app.core.config import _overlay

        monkeypatch.setitem(_overlay, "three_mf_preview_max_uncompressed_mb", 0.000001)
        with get_backend().local_path(sliced.path) as source:
            with pytest.raises(EmbeddedGcodeError, match="embedded_gcode_too_large"):
                with plate_toolpath(source, 2):
                    pytest.fail("Oversized plate was exposed")
