"""A plate proposal preserves quantity and confirms through the Multipart owner."""

import hashlib
import io
import json

from sqlmodel import select

from app.db.models import (
    FileType,
    MultipartModel,
    MultipartModelChoice,
    MultipartPart,
    SimilarityCandidate,
    SimilarityRun,
)
from app.db.session import get_session_factory
from app.modules.similarity import configuration, review, runs
from app.modules.similarity.processing import SimilarityProcessor
from app.modules.storage.storage_backend.runtime import get_backend
from app.schemas.multipart_models import MultipartPartWrite
from tests.factories.geometry import tetrahedron, three_mf


class TestComposition:
    def test_detects_six_copy_plate(self, db_session, make_user, make_model, make_file):
        actor = make_user(superuser=True)
        configuration.update_settings(
            db_session, actor, {"enabled": True, "sample_points": 256}
        )
        mesh = tetrahedron()
        plate = three_mf(
            build=[(1, f"1 0 0 0 1 0 0 0 1 {index * 40} 0 0") for index in range(6)]
        )
        backend = get_backend()
        files = []
        for content, format in (
            (mesh.export(file_type="stl"), FileType.STL),
            (plate, FileType.THREE_MF),
        ):
            file = make_file(
                make_model(),
                file_type=format,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
            file.path = backend.blob_key(
                file.model.slug, file.version, file.original_filename
            )
            db_session.add(file)
            db_session.commit()
            backend.write_stream(io.BytesIO(content), file.path)
            files.append(file)
        run = runs.start(db_session, actor)
        for _ in range(40):
            SimilarityProcessor(get_session_factory(), backend).work_one()
            db_session.expire_all()
            if db_session.get(SimilarityRun, run.id).state in runs.TERMINAL:
                break
        candidate = db_session.exec(select(SimilarityCandidate)).one()
        assert candidate.evidence_class == "plate_of"
        summary = json.loads(candidate.summary_json)
        assert summary["contained_side"] == "a"
        assert summary["copies"] == 6
        assert not candidate.exact_equivalence
        decision = review.decide(
            db_session,
            actor,
            candidate.id,
            review.DecisionRequest(
                request_id="plate-confirm",
                version=candidate.version,
                action="create_multipart",
                name="Six copies",
                parts=[
                    MultipartPartWrite(
                        name="Part", model_ids=[files[0].model_id], quantity=6
                    )
                ],
            ),
        )
        assert decision.resolution_kind == "multipart"
        aggregate = db_session.get(MultipartModel, decision.target_id)
        part = db_session.exec(
            select(MultipartPart).where(
                MultipartPart.multipart_model_id == aggregate.id
            )
        ).one()
        choice = db_session.exec(
            select(MultipartModelChoice).where(
                MultipartModelChoice.multipart_model_id == aggregate.id
            )
        ).one()
        assert part.quantity == 6
        assert choice.model_id == files[0].model_id
        assert len(db_session.exec(select(MultipartModel)).all()) == 1
