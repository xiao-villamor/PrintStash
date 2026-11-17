"""Explicit archive grouping can refer only to entries in the reviewed manifest."""

import pytest
from pydantic import ValidationError

from app.core.errors import OperationError
from app.modules.ingestion.archive_plan import (
    MultipartIntake,
    SeparateIntake,
    validate_selection,
)
from app.schemas.ingest import ArchiveEntryRead


@pytest.fixture
def entries():
    return [
        ArchiveEntryRead(
            entry_id="part", name="parts/base.stl", size_bytes=12, file_type="stl"
        ),
        ArchiveEntryRead(
            entry_id="guide", name="assembly.pdf", size_bytes=12, is_document=True
        ),
        ArchiveEntryRead(
            entry_id="image", name="photo.jpg", size_bytes=12, is_image=True
        ),
        ArchiveEntryRead(entry_id="other", name="unknown.bin", size_bytes=12),
    ]


class TestValidateSelection:
    def test_preserves_explicit_part_names(self, entries):
        plan = MultipartIntake(
            mode="multipart",
            name="Assembly",
            parts=[{"entry_name": "parts/base.stl", "name": "Base"}],
            documents=["assembly.pdf", "photo.jpg"],
        )
        validate_selection(plan, entries, ["parts/base.stl"])
        assert plan.parts[0].name == "Base"
        assert plan.documents == ["assembly.pdf", "photo.jpg"]

    @pytest.mark.parametrize(
        "name",
        ["unknown.bin", "unreviewed.pdf", "parts/base.stl"],
        ids=["unsupported", "unreviewed", "model"],
    )
    def test_refuses_a_non_document_selection(self, entries, name):
        with pytest.raises(OperationError, match="archive_document_not_found"):
            validate_selection(
                SeparateIntake(mode="separate", documents=[name]),
                entries,
                ["parts/base.stl"],
            )

    def test_refuses_duplicate_documents(self, entries):
        with pytest.raises(OperationError, match="archive_duplicate_selection"):
            validate_selection(
                SeparateIntake(
                    mode="separate", documents=["assembly.pdf", "assembly.pdf"]
                ),
                entries,
                ["parts/base.stl"],
            )

    def test_requires_every_selected_model_to_have_a_part(self, entries):
        plan = MultipartIntake(
            mode="multipart",
            name="Assembly",
            parts=[{"entry_name": "other.stl", "name": "Other"}],
        )
        with pytest.raises(OperationError, match="archive_part_selection_mismatch"):
            validate_selection(plan, entries, ["parts/base.stl"])

    def test_refuses_duplicate_part_entries(self, entries):
        plan = MultipartIntake(
            mode="multipart",
            name="Assembly",
            parts=[
                {"entry_name": "parts/base.stl", "name": "A"},
                {"entry_name": "parts/base.stl", "name": "B"},
            ],
        )
        with pytest.raises(OperationError, match="archive_duplicate_selection"):
            validate_selection(plan, entries, ["parts/base.stl"])

    @pytest.mark.parametrize(
        "name", ["", "   ", "x" * 256], ids=["empty", "blank", "long"]
    )
    def test_refuses_invalid_group_names(self, name):
        with pytest.raises(ValidationError):
            MultipartIntake(
                mode="multipart",
                name=name,
                parts=[{"entry_name": "part.stl", "name": "Part"}],
            )

    def test_refuses_more_than_a_hundred_parts(self):
        with pytest.raises(ValidationError):
            MultipartIntake(
                mode="multipart",
                name="Assembly",
                parts=[{"entry_name": f"{i}.stl", "name": str(i)} for i in range(101)],
            )

    @pytest.mark.parametrize("cover", ["assembly.pdf", "missing.jpg", "../photo.jpg"])
    def test_refuses_an_unreviewed_cover(self, entries, cover):
        plan = MultipartIntake(
            mode="multipart",
            name="Set",
            parts=[{"entry_name": "parts/base.stl", "name": "Base"}],
            cover=cover,
        )
        with pytest.raises(OperationError, match="archive_cover_not_found"):
            validate_selection(plan, entries, ["parts/base.stl"])

    def test_preserves_an_explicit_cover_choice(self, entries):
        plan = MultipartIntake(
            mode="multipart",
            name="Set",
            parts=[{"entry_name": "parts/base.stl", "name": "Base"}],
            cover="photo.jpg",
        )
        validate_selection(plan, entries, ["parts/base.stl"])
        assert plan.cover == "photo.jpg"
