"""Deterministic, bounded passage text, independent of an inference tokenizer.

Recipe tokens are Unicode words or punctuation, not an ML model's token IDs.
Embedding adapters must apply their own sequence limit and report truncation.
Changing these limits, the field order or normalization requires a new recipe.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import cast

RECIPE_VERSION = 1
CHUNK_TOKENS = 400
OVERLAP_TOKENS = 40
MAX_CHUNKS = 16
MAX_TEXT_CHARS = 131_072
MAX_PASSAGE_CHARS = 16_384
MAX_FIELD_CHARS = 4_096
MAX_FIELD_ITEMS = 64
_TOKENS = re.compile(r"\w+|[^\w\s]", re.UNICODE)


class SubjectType(str, Enum):
    MODEL = "model"
    COLLECTION = "collection"
    MULTIPART_MODEL = "multipart_model"
    DOCUMENT = "document"


@dataclass(frozen=True, order=True)
class SearchSubject:
    subject_type: SubjectType
    subject_id: int

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.subject_type), SubjectType):
            raise ValueError("invalid search subject type")
        if type(self.subject_id) is not int or not 0 < self.subject_id < 2**63:
            raise ValueError("invalid search subject id")


@dataclass(frozen=True)
class PassageContent:
    """Recipe-v1 field list; extraction adapters supply authorized segments."""

    title: str
    collection: str = ""
    tags: tuple[str, ...] = ()
    description: str = ""
    filenames: tuple[str, ...] = ()
    revisions: tuple[str, ...] = ()
    source_titles: tuple[str, ...] = ()
    source_summaries: tuple[str, ...] = ()
    source_tags: tuple[str, ...] = ()
    parts: tuple[str, ...] = ()
    choices: tuple[str, ...] = ()
    body: str = ""
    caption: str = ""


@dataclass(frozen=True)
class RenderedPassage:
    chunk_index: int
    text: str
    content_hash: str
    truncated: bool


def access_identity(dependencies: tuple[SearchSubject, ...]) -> tuple[str, str]:
    """Canonical conjunctive access requirements, never user IDs.

    The Subject's own authorization is always required in addition to these
    contributors. A digest identifies a segment; it is not an access decision.
    """
    encoded = json.dumps(
        [[ref.subject_type.value, ref.subject_id] for ref in sorted(set(dependencies))],
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode()).hexdigest(), encoded


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # PostgreSQL Text refuses NUL. Other controls have no searchable meaning.
    return unicodedata.normalize(
        "NFC",
        "".join(c for c in text if c in "\n\t" or unicodedata.category(c) != "Cc"),
    ).strip()


def render_passages(
    content: PassageContent, *, recipe_version: int = RECIPE_VERSION
) -> tuple[RenderedPassage, ...]:
    """Render labelled metadata once per passage with overlapping body windows.

    Metadata has a separate budget, so filenames cannot consume the entire
    description allowance. Every discarded input is reported via ``truncated``.
    """
    if type(recipe_version) is not int or recipe_version not in (1, 2):
        raise ValueError("search_recipe_unavailable")
    truncated = False
    lines: list[str] = []
    fields: tuple[tuple[str, str | tuple[str, ...]], ...] = (
        ("Title", content.title),
        ("Collection", content.collection),
        ("Tags", content.tags),
        ("Files", content.filenames),
        ("Revisions", content.revisions),
        ("Source titles", content.source_titles),
        ("Source summaries", content.source_summaries),
        ("Source tags", content.source_tags),
        ("Multipart Parts", content.parts),
        ("Model Choices", content.choices),
    )
    if recipe_version == 2:
        truncated |= len(content.caption) > 2048
        fields += (("AI caption", content.caption[:2048]),)
    for label, raw in fields:
        if isinstance(raw, tuple):
            truncated |= len(raw) > MAX_FIELD_ITEMS
            value = "; ".join(
                dict.fromkeys(
                    " ".join(_clean(entry[:MAX_FIELD_CHARS]).split())
                    for entry in raw[:MAX_FIELD_ITEMS]
                    if entry.strip()
                )
            )
            truncated |= any(
                len(entry) > MAX_FIELD_CHARS for entry in raw[:MAX_FIELD_ITEMS]
            )
        else:
            value = " ".join(_clean(raw[:MAX_FIELD_CHARS]).split())
            truncated |= len(raw) > MAX_FIELD_CHARS
        truncated |= len(value) > MAX_FIELD_CHARS
        if value:
            lines.append(f"{label}: {value[:MAX_FIELD_CHARS]}")
    # A bounded prefix reserves space for body text even with every field present.
    metadata = "\n".join(lines)
    truncated |= len(metadata) > MAX_PASSAGE_CHARS // 2
    metadata = metadata[: MAX_PASSAGE_CHARS // 2]
    body_parts: list[str] = []
    for label, raw in (("Description", content.description), ("Body", content.body)):
        truncated |= len(raw) > MAX_TEXT_CHARS
        value = _clean(raw[:MAX_TEXT_CHARS])
        if value:
            body_parts.append(f"{label}: {value}")
    body = "\n".join(body_parts)
    truncated |= len(body) > MAX_TEXT_CHARS
    body = body[:MAX_TEXT_CHARS]
    spans = tuple(_TOKENS.finditer(body))
    windows: list[str] = []
    start = 0
    end = 0
    while start < len(spans) and len(windows) < MAX_CHUNKS:
        end = min(start + CHUNK_TOKENS, len(spans))
        windows.append(body[spans[start].start() : spans[end - 1].end()])
        if end == len(spans):
            break
        start = end - OVERLAP_TOKENS
    truncated |= end < len(spans)
    texts = [
        "\n".join(part for part in (metadata, window) if part)
        for window in (windows or [""])
    ]
    truncated |= any(len(text) > MAX_PASSAGE_CHARS for text in texts)
    return tuple(
        RenderedPassage(
            chunk_index=index,
            text=text[:MAX_PASSAGE_CHARS],
            content_hash=hashlib.sha256(
                f"{recipe_version}\0{text[:MAX_PASSAGE_CHARS]}".encode()
            ).hexdigest(),
            truncated=truncated,
        )
        for index, text in enumerate(texts)
    )
