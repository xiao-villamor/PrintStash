"""Review acceptance must survive restart, preserve ownership and commit once."""

import json
from datetime import timedelta

from app.core.time import utcnow
from app.db.models import IngestionReview
from app.db.session import get_session_factory
from app.modules.ingestion import background, importer, review_manifests


class TestReviewManifestsContract:
    def test_restores_a_private_review_in_a_new_registry(self, db_session, make_user):
        owner = make_user()
        token = background.pending_model_files.add(
            background._PendingModelFiles(
                page_url="https://example.com/model",
                page_title="Model",
                owner_user_id=owner.id,
                files=[
                    background.import_resolvers.ModelFile(
                        file_id="one", name="one.stl", file_type="stl", size=100
                    )
                ],
            )
        )
        restored = background._PendingRegistry().get(token)
        assert restored.owner_user_id == owner.id
        assert restored.files[0].name == "one.stl"

    def test_expired_reviews_are_unavailable(self, make_ingestion_review):
        row = make_ingestion_review(expired=True)
        assert review_manifests.get(row.kind, row.id) is None

    def test_acceptance_can_be_retried_after_rollback(
        self, db_session, make_ingestion_review
    ):
        row = make_ingestion_review()
        assert review_manifests.consume(db_session, row.kind, row.id)
        db_session.rollback()
        assert review_manifests.get(row.kind, row.id) is not None

    def test_committed_acceptance_cannot_be_replayed(
        self, db_session, make_ingestion_review
    ):
        row = make_ingestion_review()
        kind, token = row.kind, row.id
        review_manifests.consume(db_session, kind, token)
        db_session.commit()
        with get_session_factory().scoped_session() as fresh:
            assert review_manifests.consume(fresh, kind, token) is False

    def test_archive_claim_is_exclusive(self, make_ingestion_review):
        row = make_ingestion_review(
            kind="archive", payload_json=json.dumps({"archive_name": "models.zip"})
        )
        review_manifests.get(row.kind, row.id, claim=True)
        assert review_manifests.get(row.kind, row.id, claim=True) is None

    def test_recovers_an_expired_archive_claim(self, make_ingestion_review):
        row = make_ingestion_review(
            kind="archive", claim_expires_at=utcnow() - timedelta(minutes=1)
        )
        assert review_manifests.get(row.kind, row.id, claim=True) is not None

    def test_expiring_a_review_preserves_foreign_staged_bytes(
        self, db_session, tmp_path, make_user
    ):
        staged = tmp_path / "archive.zip"
        staged.write_bytes(b"original")
        token = importer.archives.add(
            importer.PendingArchive(
                path=staged,
                archive_name="archive.zip",
                owner_user_id=make_user().id,
                entries=[],
                created_at=0,
            )
        )
        staged.unlink()
        staged.write_bytes(b"replacement")
        review_manifests.prune_expired(db_session)
        db_session.commit()
        assert db_session.get(IngestionReview, token) is None
        assert staged.read_bytes() == b"replacement"
