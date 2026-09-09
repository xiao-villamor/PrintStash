"""The ingestion strategy preserves the declared Artifact type at parser seams."""

from pathlib import Path

from app.db.models import FileType
from app.modules.ingestion import ingestion


class TestMeshStrategy:
    def test_passes_declared_type_for_suffixless_staging(self, monkeypatch) -> None:
        observed: dict[str, object] = {}

        def analyze(path: Path, **options):
            observed.update(path=path, **options)
            return {}, None

        monkeypatch.setattr(ingestion.mesh_operations, "analyze_mesh", analyze)

        ingestion.strategy_for_artifact(FileType.STL).process(Path("assembled.upload"))

        assert observed["file_type"] == "stl"
        assert observed["path"] == Path("assembled.upload")
