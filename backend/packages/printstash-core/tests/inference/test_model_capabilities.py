"""Only a reviewed immutable model identity can advertise Matryoshka truncation points."""

from dataclasses import replace

import pytest

from printstash_core.inference import EmbeddingSpace
from printstash_core.inference.model_capabilities import (
    MXBAI_LARGE_V1,
    capabilities_for,
)


@pytest.fixture
def reviewed_space():
    return EmbeddingSpace(
        model_key="local-server-alias",
        model_repo=MXBAI_LARGE_V1.repository,
        model_revision=MXBAI_LARGE_V1.revision,
        dimension=1024,
        modality="text",
        render_recipe="test",
    )


class TestCapabilitiesFor:
    def test_recognizes_the_pinned_publisher_model(self, reviewed_space):
        assert capabilities_for(reviewed_space).mrl_dimensions == (64, 128, 256, 512)

    @pytest.mark.parametrize(
        "changed",
        [
            {"model_repo": None},
            {"model_revision": "main"},
            {"dimension": 384},
            {"model_repo": "different/model"},
        ],
        ids=["undeclared", "moving-revision", "different-dimension", "different-model"],
    )
    def test_refuses_unreviewed_model_identities(self, reviewed_space, changed):
        assert capabilities_for(replace(reviewed_space, **changed)) is None
