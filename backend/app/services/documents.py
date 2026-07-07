"""Shared document classification — used by both the documents API (manual
uploads) and the external-library scanner (folder-discovered PDFs/markdown).

Kept separate from api/v1/documents.py so services don't reach into the API
layer to use it.
"""

from __future__ import annotations

from app.db.models import DocumentKind

# Suffixes the scanner treats as document candidates (services/external_library.py)
# and the upload endpoint classifies the same way (api/v1/documents.py). Anything
# else that gets manually uploaded still lands as DocumentKind.OTHER; the scanner
# only picks up these — an arbitrary binary sitting in a NAS folder isn't
# something we can meaningfully classify or preview, so it's left alone.
MARKDOWN_EXTS = {".md", ".markdown", ".txt"}
BINARY_MEDIA_TYPES = {".pdf": "application/pdf"}

# What the scanner actually looks for on disk, mapped to the resulting kind.
DOCUMENT_SUFFIX_TO_KIND: dict[str, DocumentKind] = {
    ".md": DocumentKind.MARKDOWN,
    ".markdown": DocumentKind.MARKDOWN,
    ".txt": DocumentKind.MARKDOWN,
    ".pdf": DocumentKind.PDF,
}


def kind_for(ext: str) -> DocumentKind:
    """Classify a lowercase extension (with leading dot) for a manually
    uploaded document. Unlike DOCUMENT_SUFFIX_TO_KIND, this always returns a
    kind — anything not recognised is OTHER (still uploadable, just not
    something the scanner would have found on its own)."""
    if ext in MARKDOWN_EXTS:
        return DocumentKind.MARKDOWN
    if ext == ".pdf":
        return DocumentKind.PDF
    return DocumentKind.OTHER
