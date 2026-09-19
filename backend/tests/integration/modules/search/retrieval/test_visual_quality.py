"""Replay measured real CLIP vectors through the shipped authorized store.

The 32 queries were frozen before running models. This is an engineering corpus,
not independent human acceptance. The separate Benchy case replays an
unmodified, attributed photograph of a physical print.
"""

import base64
import hashlib
import json
import struct

import pytest
from printstash_core.inference import EmbeddingSpace
from printstash_core.search.visual_inputs import VisualRecipe, mean_pool

from app.db.models import FileType
from app.modules.search import semantic, vector_store, visual_sources
from app.schemas.inference import SearchSettings
from tests.paths import FIXTURES_DIR, REPO_ROOT


def vectors(item, style):
    if style == "existing_media":
        payload = json.loads(
            (
                FIXTURES_DIR / "search/clip-b32-existing-thumbnail-vectors.json"
            ).read_text()
        )
        row = next(value for value in payload["items"] if value["id"] == item["id"])
        assert row["mesh_sha256"] == item["mesh_sha256"]
        return (
            struct.unpack(
                "<512f",
                base64.b64decode(row["vector_float32_le_base64"], validate=True),
            ),
        )
    data = base64.b64decode(
        item["views"][style]["vectors_float32_le_base64"], validate=True
    )
    return tuple(
        struct.unpack("<512f", data[index : index + 2048])
        for index in range(0, len(data), 2048)
    )


class TestVisualRanking:
    @pytest.mark.parametrize(
        "style,aggregation,expected",
        [
            ("existing_media", "mean", 27 / 32),
            ("thumbnail_catalog", "mean", 26 / 32),
            ("thumbnail_matte", "mean", 26 / 32),
            ("multiview_matte", "mean", 20 / 32),
            ("multiview_matte", "max", 23 / 32),
        ],
    )
    def test_replays_real_visual_ranking(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        make_index_generation,
        make_passage_vector,
        style,
        aggregation,
        expected,
    ):
        root = FIXTURES_DIR / "search"
        payload = json.loads((root / "clip-b32-visual-vectors.json").read_text())
        assert (
            payload["query_manifest_sha256"]
            == hashlib.sha256((root / "visual-queries.json").read_bytes()).hexdigest()
        )
        assert (
            payload["generator_sha256"]
            == hashlib.sha256(
                (REPO_ROOT / "backend/tests/fakes/visual_corpus.py").read_bytes()
            ).hexdigest()
        )
        encoder = EmbeddingSpace(**payload["space"])
        assert encoder.config_hash == payload["space_hash"]
        profile = "multiview" if style.startswith("multiview") else "thumbnail"
        space = VisualRecipe.space(
            encoder, image_size=224, profile=profile, aggregation=aggregation
        )
        actor = make_user(superuser=True)
        stored = vector_store.register_space(db_session, space)
        generation = make_index_generation(
            stored,
            index_backend="numpy",
            effective_backend="numpy",
            index_dimension=512,
        )
        expected_models = {}
        for item in payload["items"]:
            # Opaque names and no tags/descriptions: lexical signals cannot explain a hit.
            model = make_model(f"Object {item['id']}")
            file = make_file(model, file_type=FileType.STL, sha256=item["mesh_sha256"])
            expected_models[item["id"]] = model.id
            views = vectors(item, style)
            units = [("visual_mean", "mean", mean_pool(views, 512))]
            if profile == "multiview":
                units += [
                    ("visual_view", f"view:{i}", vector)
                    for i, vector in enumerate(views)
                ]
                units.append(
                    (
                        "visual_thumbnail",
                        "thumbnail",
                        vectors(item, "thumbnail_matte")[0],
                    )
                )
            for kind, key, vector in units:
                make_passage_vector(
                    generation,
                    file,
                    unit_kind=kind,
                    unit_key=f"file:{file.id}:{key}",
                    vector_blob=struct.pack("<512f", *vector),
                )
        db_session.commit()
        floor = semantic.score_floor(space, SearchSettings())
        assert floor == 0.2
        assert (
            semantic.score_floor(
                space, SearchSettings(semantic_floors={space.config_hash: 0.6})
            )
            == 0.6
        )
        found = 0
        for item in payload["items"]:
            query = struct.unpack(
                "<512f",
                base64.b64decode(item["query_float32_le_base64"], validate=True),
            )
            result = vector_store.query(
                db_session,
                generation_id=generation.id,
                space=space,
                vector=query,
                allowed_ids=visual_sources.current_vectors(
                    db_session, generation.id, space, actor
                ),
                limit=5,
            )
            assert not result.truncated
            found += expected_models[item["id"]] in {
                hit.subject_id for hit in result.items if hit.score >= floor
            }
        assert found / 32 == expected


class TestPointRanking:
    def test_replays_real_point_quality_gain(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        make_index_generation,
        make_passage_vector,
    ):
        root = FIXTURES_DIR / "search"
        point = json.loads((root / "openshape-b32-point-vectors.json").read_text())
        visual = json.loads((root / "clip-b32-visual-vectors.json").read_text())
        space = EmbeddingSpace(**point["space"])
        assert space.config_hash == point["space_hash"]
        assert point["query_manifest_sha256"] == visual["query_manifest_sha256"]
        assert point["generator_sha256"] == visual["generator_sha256"]
        assert space.alignment_identity == visual["space_hash"]
        assert semantic.score_floor(space, SearchSettings()) == 0.1
        assert (
            semantic.score_floor(
                space, SearchSettings(semantic_floors={space.config_hash: 0.6})
            )
            == 0.6
        )
        actor = make_user(superuser=True)
        stored = vector_store.register_space(db_session, space)
        generation = make_index_generation(
            stored,
            index_backend="numpy",
            effective_backend="numpy",
            index_dimension=512,
        )
        expected = {}
        for row in point["items"]:
            model = make_model(f"Point object {row['id']}")
            file = make_file(model, file_type=FileType.STL, sha256=row["mesh_sha256"])
            expected[row["id"]] = model.id
            make_passage_vector(
                generation,
                file,
                unit_kind="point_cloud",
                unit_key=f"file:{file.id}:point",
                vector_blob=base64.b64decode(
                    row["vector_float32_le_base64"], validate=True
                ),
            )
        db_session.commit()
        found = 0
        for item in visual["items"]:
            query = struct.unpack(
                "<512f",
                base64.b64decode(item["query_float32_le_base64"], validate=True),
            )
            result = vector_store.query(
                db_session,
                generation_id=generation.id,
                space=space,
                vector=query,
                allowed_ids=visual_sources.current_vectors(
                    db_session, generation.id, space, actor
                ),
                limit=10,
            )
            found += expected[item["id"]] in {
                hit.subject_id
                for hit in result.items
                if hit.score >= semantic.score_floor(space, SearchSettings())
            }
        assert found == 30
        assert found / 32 > 0.84375  # Same corpus, measured existing thumbnail @10.


class TestPrintedPhotoRanking:
    @pytest.mark.parametrize(
        "profile,aggregation,expected_rank",
        [("thumbnail", "mean", 13), ("multiview", "mean", 1), ("multiview", "max", 2)],
    )
    def test_replays_printed_photo_rank(
        self,
        db_session,
        make_user,
        make_model,
        make_file,
        make_index_generation,
        make_passage_vector,
        profile,
        aggregation,
        expected_rank,
    ):
        root = FIXTURES_DIR / "search"
        measured = json.loads((root / "printed-benchy-vectors.json").read_text())
        corpus = json.loads((root / "clip-b32-visual-vectors.json").read_text())
        assert (
            measured["photo_sha256"]
            == hashlib.sha256((root / "printed-benchy.jpg").read_bytes()).hexdigest()
        )
        assert (
            measured["mesh_sha256"]
            == hashlib.sha256(
                (REPO_ROOT / "testdata/benchy/3dbenchy.stl").read_bytes()
            ).hexdigest()
        )
        assert (
            measured["distractors_sha256"]
            == hashlib.sha256(
                (root / "clip-b32-existing-thumbnail-vectors.json").read_bytes()
            ).hexdigest()
        )
        assert (
            measured["multiview_distractors_sha256"]
            == hashlib.sha256(
                (root / "clip-b32-visual-vectors.json").read_bytes()
            ).hexdigest()
        )
        encoder = EmbeddingSpace(**measured["space"])
        assert encoder.config_hash == measured["space_hash"] == corpus["space_hash"]
        space = VisualRecipe.space(
            encoder, image_size=224, profile=profile, aggregation=aggregation
        )
        actor = make_user(superuser=True)
        stored = vector_store.register_space(db_session, space)
        generation = make_index_generation(
            stored,
            index_backend="numpy",
            effective_backend="numpy",
            index_dimension=512,
        )
        rows = [
            (
                item["mesh_sha256"],
                vectors(
                    item,
                    "existing_media" if profile == "thumbnail" else "multiview_matte",
                ),
            )
            for item in corpus["items"]
        ]
        payload = base64.b64decode(
            measured[
                "model_float32_le_base64"
                if profile == "thumbnail"
                else "model_views_float32_le_base64"
            ],
            validate=True,
        )
        rows.append(
            (measured["mesh_sha256"], tuple(struct.iter_unpack("<512f", payload)))
        )
        assert len(rows) == measured["candidate_count"] == 33
        target_id = None
        for index, (digest, views) in enumerate(rows):
            model = make_model(f"Photographic candidate {index}")
            file = make_file(model, file_type=FileType.STL, sha256=digest)
            target_id = model.id
            units = [("visual_mean", "mean", mean_pool(views, 512))]
            if profile == "multiview":
                units += [
                    ("visual_view", f"view:{i}", vector)
                    for i, vector in enumerate(views)
                ]
            for kind, key, vector in units:
                make_passage_vector(
                    generation,
                    file,
                    unit_kind=kind,
                    unit_key=f"file:{file.id}:{key}",
                    vector_blob=struct.pack("<512f", *vector),
                )
            if profile == "multiview":
                fallback = (
                    struct.unpack(
                        "<512f",
                        base64.b64decode(
                            measured["model_float32_le_base64"], validate=True
                        ),
                    )
                    if index == len(rows) - 1
                    else vectors(corpus["items"][index], "existing_media")[0]
                )
                make_passage_vector(
                    generation,
                    file,
                    unit_kind="visual_thumbnail",
                    unit_key=f"file:{file.id}:thumbnail",
                    vector_blob=struct.pack("<512f", *fallback),
                )
        db_session.commit()
        query = struct.unpack(
            "<512f",
            base64.b64decode(measured["query_float32_le_base64"], validate=True),
        )
        result = vector_store.query(
            db_session,
            generation_id=generation.id,
            space=space,
            vector=query,
            allowed_ids=visual_sources.current_vectors(
                db_session, generation.id, space, actor
            ),
            limit=33,
        )
        assert not result.truncated
        hits = [
            hit
            for hit in result.items
            if hit.score >= semantic.score_floor(space, SearchSettings())
        ]
        rank = next(i + 1 for i, hit in enumerate(hits) if hit.subject_id == target_id)
        recorded = (
            measured if profile == "thumbnail" else measured["multiview"][aggregation]
        )
        assert rank == recorded["rank"] == expected_rank
        assert hits[rank - 1].score == pytest.approx(recorded["score"], abs=1e-6)
        if profile == "multiview":
            assert rank <= 10
        else:
            assert rank > 10  # Retain the measured full-scene thumbnail limitation.
