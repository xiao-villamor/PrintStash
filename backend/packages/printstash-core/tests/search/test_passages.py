"""Passage recipes preserve searchable text deterministically within fixed bounds.

These tests defend the text contract before an inference tokenizer or database
can change it, including explicit reporting whenever source content is lost.
"""

from dataclasses import replace

import pytest

from printstash_core.search.passages import (
    CHUNK_TOKENS,
    MAX_CHUNKS,
    MAX_FIELD_CHARS,
    MAX_FIELD_ITEMS,
    MAX_PASSAGE_CHARS,
    MAX_TEXT_CHARS,
    OVERLAP_TOKENS,
    PassageContent,
    SearchSubject,
    SubjectType,
    access_identity,
    render_passages,
)


class TestSearchSubject:
    @pytest.mark.parametrize("kind", list(SubjectType), ids=lambda kind: kind.value)
    def test_accepts_each_supported_subject(self, kind):
        assert SearchSubject(kind, 1).subject_type == kind

    @pytest.mark.parametrize(
        "subject_id",
        [0, -1, 2**63, True, 1.5, "1"],
        ids=["zero", "negative", "overflow", "boolean", "float", "string"],
    )
    def test_rejects_invalid_subject_ids(self, subject_id):
        with pytest.raises(ValueError, match="invalid search subject id"):
            SearchSubject(SubjectType.MODEL, subject_id)

    def test_rejects_an_unsupported_subject_type(self):
        with pytest.raises(ValueError, match="invalid search subject type"):
            SearchSubject("artifact", 1)


class TestAccessIdentity:
    def test_canonicalizes_conjunctive_access_dependencies(self):
        model = SearchSubject(SubjectType.MODEL, 1)
        collection = SearchSubject(SubjectType.COLLECTION, 2)

        first = access_identity((model, collection, model))
        second = access_identity((collection, model))

        assert first == second
        assert first[1] == '[["collection",2],["model",1]]'

    def test_distinguishes_subject_types_with_equal_ids(self):
        assert access_identity(
            (SearchSubject(SubjectType.MODEL, 1),)
        ) != access_identity((SearchSubject(SubjectType.COLLECTION, 1),))

    def test_represents_a_subject_only_segment(self):
        assert access_identity(())[1] == "[]"


class TestRenderPassages:
    def test_renders_every_recipe_field_in_stable_order(self):
        content = PassageContent(
            title="Dragon",
            collection="toys/dragons",
            tags=("flexible",),
            description="No supports",
            filenames=("dragon.stl",),
            revisions=("Draft revision",),
            source_titles=("Articulated dragon",),
            source_summaries=("Print in place",),
            source_tags=("toy",),
            parts=("Tail",),
            choices=("Short tail",),
            body="Assembly notes",
        )

        passages = render_passages(content)

        assert (
            passages[0].text
            == "Title: Dragon\nCollection: toys/dragons\nTags: flexible\nFiles: dragon.stl\nRevisions: Draft revision\nSource titles: Articulated dragon\nSource summaries: Print in place\nSource tags: toy\nMultipart Parts: Tail\nModel Choices: Short tail\nDescription: No supports\nBody: Assembly notes"
        )
        assert not passages[0].truncated

    def test_preserves_unicode_text(self):
        passage = render_passages(
            PassageContent(
                title="Cafe\u0301\0\r\n支架", body="Soporte español\n\n**Cable**"
            )
        )[0]

        assert passage.text == "Title: Café 支架\nBody: Soporte español\n\n**Cable**"

    def test_omits_empty_fields(self):
        assert render_passages(PassageContent(title="Box"))[0].text == "Title: Box"

    def test_deduplicates_repeated_list_values(self):
        passage = render_passages(
            PassageContent(title="Box", tags=("lid", "lid", " ", "hinge"))
        )[0]

        assert passage.text == "Title: Box\nTags: lid; hinge"

    def test_chunks_a_document_body_at_the_token_cap_with_overlap(self):
        content = PassageContent(
            title="Guide", body=" ".join(f"word{i}" for i in range(500))
        )

        passages = render_passages(content)

        assert len(passages) == 2
        # Body and ':' occupy two recipe tokens in the first window.
        first_words = passages[0].text.split("Body: ", 1)[1].split()
        second_words = passages[1].text.split("\n", 1)[1].split()
        assert first_words[-OVERLAP_TOKENS:] == second_words[:OVERLAP_TOKENS]
        assert first_words[0] == "word0"
        assert second_words[-1] == "word499"
        assert len(first_words) == CHUNK_TOKENS - 2

    def test_keeps_a_body_at_the_token_boundary_in_one_passage(self):
        passages = render_passages(
            PassageContent(title="Guide", body="word " * (CHUNK_TOKENS - 2))
        )

        assert len(passages) == 1
        assert not passages[0].truncated

    def test_caps_the_number_of_chunks_for_an_oversized_document(self):
        passages = render_passages(
            PassageContent(title="Guide", body="word " * (CHUNK_TOKENS * MAX_CHUNKS))
        )

        assert len(passages) == MAX_CHUNKS
        assert all(passage.truncated for passage in passages)

    @pytest.mark.parametrize(
        "content",
        [
            PassageContent(title="x" * (MAX_FIELD_CHARS + 1)),
            PassageContent(title="Box", filenames=("x",) * (MAX_FIELD_ITEMS + 1)),
            PassageContent(title="Box", body="x" * (MAX_TEXT_CHARS + 1)),
            PassageContent(title="Box", tags=("x" * (MAX_FIELD_CHARS + 1),)),
            PassageContent(title="Box", body="x" * MAX_PASSAGE_CHARS),
        ],
        ids=["title", "list-count", "body", "list-entry", "long-token"],
    )
    def test_reports_bounded_truncation(self, content):
        passages = render_passages(content)

        assert all(passage.truncated for passage in passages)
        assert max(len(passage.text) for passage in passages) <= MAX_PASSAGE_CHARS

    def test_reserves_body_space_when_metadata_fills_its_budget(self):
        content = PassageContent(
            title="x" * MAX_FIELD_CHARS,
            collection="y" * MAX_FIELD_CHARS,
            tags=("z" * MAX_FIELD_CHARS,),
            body="Meaningful body",
        )

        passage = render_passages(content)[0]

        assert passage.text.endswith("Body: Meaningful body")
        assert passage.truncated

    def test_changes_the_hash_when_text_changes(self):
        original = PassageContent(title="Box")

        assert (
            render_passages(original)[0].content_hash
            != render_passages(replace(original, title="Lid"))[0].content_hash
        )

    def test_keeps_identical_text_hashes_stable(self):
        assert render_passages(PassageContent(title="Box")) == render_passages(
            PassageContent(title=" Box ")
        )

    def test_accepts_empty_content(self):
        passages = render_passages(PassageContent(title=""))

        assert len(passages) == 1
        assert passages[0].text == ""
        assert not passages[0].truncated


class TestCaptionRecipe:
    def test_preserves_v1_hashes_when_a_caption_exists(self):
        plain = PassageContent(title="Human title", description="Human description")
        captioned = PassageContent(
            title="Human title",
            description="Human description",
            caption="AI geometry label",
        )
        assert render_passages(captioned, recipe_version=1) == render_passages(plain)

    def test_renders_caption_as_a_separate_v2_field(self):
        content = PassageContent(
            title="Human title",
            description="Human description",
            caption="AI geometry label",
        )
        result = render_passages(content, recipe_version=2)
        assert "AI caption: AI geometry label" in result[0].text
        assert "Description: Human description" in result[0].text
        assert (
            result[0].content_hash
            != render_passages(content, recipe_version=1)[0].content_hash
        )

    def test_bounds_caption_text_in_the_passage(self):
        result = render_passages(
            PassageContent(title="Object", caption="x" * 5000), recipe_version=2
        )
        assert result[0].truncated
        assert len(result[0].text.split("AI caption: ")[1].splitlines()[0]) == 2048
