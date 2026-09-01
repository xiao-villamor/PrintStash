"""Public G-code metadata and binary-container API."""

from . import bgcode
from .bgcode import (
    MAGIC,
    THUMBNAIL_FORMATS,
    is_bgcode,
    is_valid_container,
    iter_thumbnails,
    read_metadata_text,
)
from .formats import (
    PrintArtifactFormatError,
    classify_print_artifact,
    content_type_for_format,
    declared_print_artifact_format,
)
from .models import (
    GcodeMetadata,
    LegacyGcodeMetadata,
    LegacyMaterialRequirement,
    MaterialRequirement,
    to_legacy_dict,
)
from .parser import parse, parse_duration

__all__ = [
    "GcodeMetadata",
    "LegacyGcodeMetadata",
    "LegacyMaterialRequirement",
    "MAGIC",
    "MaterialRequirement",
    "PrintArtifactFormatError",
    "THUMBNAIL_FORMATS",
    "bgcode",
    "classify_print_artifact",
    "content_type_for_format",
    "declared_print_artifact_format",
    "is_bgcode",
    "is_valid_container",
    "iter_thumbnails",
    "parse",
    "parse_duration",
    "read_metadata_text",
    "to_legacy_dict",
]
