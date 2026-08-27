"""Model stars remain idempotent, per-user, and collection-access scoped."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import (
    Collection,
    CollectionPermission,
    CollectionRole,
    Model,
    ModelStar,
    User,
)
from app.services.auth import create_access_token, hash_password


def _user_headers(db_session: Session, username: str) -> tuple[User, dict[str, str]]:
    user = User(
        username=username,
        hashed_password=hash_password("Password123"),
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    token = create_access_token(user.id, user.username, scope="write")
    return user, {"Authorization": f"Bearer {token}"}


def _model_with_access(
    db_session: Session, user: User, *, suffix: str = "star"
) -> Model:
    collection = Collection(
        name=f"Collection {suffix}",
        slug=f"collection-{suffix}",
        path=f"collection-{suffix}",
    )
    db_session.add(collection)
    db_session.commit()
    db_session.refresh(collection)
    db_session.add(
        CollectionPermission(
            user_id=user.id,
            collection_id=collection.id,
            role=CollectionRole.VIEW,
        )
    )
    model = Model(
        name=f"Model {suffix}",
        slug=f"model-{suffix}",
        hash=f"{suffix:0<64}"[:64],
        collection_id=collection.id,
    )
    db_session.add(model)
    db_session.commit()
    db_session.refresh(model)
    return model


class TestStarModel:
    def test_stars_a_live_accessible_model_for_the_current_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "star-owner")
        model = _model_with_access(db_session, user)

        response = client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        row = db_session.exec(
            select(ModelStar).where(
                ModelStar.user_id == user.id, ModelStar.model_id == model.id
            )
        ).one()
        assert response.status_code == 200, response.text
        assert response.json() == {"model_id": model.id, "starred": True}
        assert row.model_id == model.id

    def test_treats_repeated_star_requests_idempotently(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "repeat-star-owner")
        model = _model_with_access(db_session, user, suffix="repeat")

        first = client.put(f"/api/v1/models/{model.id}/star", headers=headers)
        second = client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        rows = db_session.exec(
            select(ModelStar).where(
                ModelStar.user_id == user.id, ModelStar.model_id == model.id
            )
        ).all()
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert len(rows) == 1

    def test_isolates_stars_by_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        first, first_headers = _user_headers(db_session, "star-user-one")
        second, second_headers = _user_headers(db_session, "star-user-two")
        model = _model_with_access(db_session, first, suffix="isolation")
        db_session.add(
            CollectionPermission(
                user_id=second.id,
                collection_id=model.collection_id,
                role=CollectionRole.VIEW,
            )
        )
        db_session.commit()

        client.put(f"/api/v1/models/{model.id}/star", headers=first_headers)
        response = client.put(f"/api/v1/models/{model.id}/star", headers=second_headers)

        stars = db_session.exec(
            select(ModelStar).where(ModelStar.model_id == model.id)
        ).all()
        assert response.status_code == 200, response.text
        assert {star.user_id for star in stars} == {first.id, second.id}

    def test_hides_a_missing_model_from_star(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "star-denied")

        response = client.put("/api/v1/models/999999/star", headers=headers)

        assert response.status_code == 404, response.text
        assert (
            db_session.exec(select(ModelStar).where(ModelStar.user_id == user.id)).all()
            == []
        )

    def test_hides_a_trashed_model_from_star(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "star-trashed")
        model = _model_with_access(db_session, user, suffix="trashed-star")
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        response = client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        assert response.status_code == 404, response.text
        assert db_session.exec(select(ModelStar)).all() == []

    def test_rejects_unauthenticated_star_mutation(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, _headers = _user_headers(db_session, "unauth-star-owner")
        model = _model_with_access(db_session, user, suffix="unauth")

        response = client.put(f"/api/v1/models/{model.id}/star")

        assert response.status_code == 401, response.text
        assert db_session.exec(select(ModelStar)).all() == []


class TestUnstarModel:
    def test_unstars_a_model_for_the_current_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "unstar-owner")
        model = _model_with_access(db_session, user, suffix="unstar")
        client.put(f"/api/v1/models/{model.id}/star", headers=headers)

        response = client.delete(f"/api/v1/models/{model.id}/star", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json() == {"model_id": model.id, "starred": False}
        assert db_session.exec(select(ModelStar)).all() == []

    def test_treats_repeated_unstar_requests_idempotently(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "repeat-unstar-owner")
        model = _model_with_access(db_session, user, suffix="repeat-unstar")

        response = client.delete(f"/api/v1/models/{model.id}/star", headers=headers)

        assert response.status_code == 200, response.text
        assert response.json()["starred"] is False

    def test_preserves_another_users_star_when_unstarred_by_current_user(
        self, client: TestClient, db_session: Session
    ) -> None:
        first, first_headers = _user_headers(db_session, "preserve-star-one")
        second, second_headers = _user_headers(db_session, "preserve-star-two")
        model = _model_with_access(db_session, first, suffix="preserve")
        db_session.add(
            CollectionPermission(
                user_id=second.id,
                collection_id=model.collection_id,
                role=CollectionRole.VIEW,
            )
        )
        db_session.commit()
        client.put(f"/api/v1/models/{model.id}/star", headers=first_headers)

        response = client.delete(
            f"/api/v1/models/{model.id}/star", headers=second_headers
        )

        stars = db_session.exec(
            select(ModelStar).where(ModelStar.model_id == model.id)
        ).all()
        assert response.status_code == 200, response.text
        assert [star.user_id for star in stars] == [first.id]

    def test_hides_a_missing_model_from_unstar(
        self, client: TestClient, db_session: Session
    ) -> None:
        _, headers = _user_headers(db_session, "unstar-denied")

        response = client.delete("/api/v1/models/999999/star", headers=headers)

        assert response.status_code == 404, response.text

    def test_hides_a_trashed_model_from_unstar(
        self, client: TestClient, db_session: Session
    ) -> None:
        user, headers = _user_headers(db_session, "unstar-trashed")
        model = _model_with_access(db_session, user, suffix="trashed-unstar")
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        response = client.delete(f"/api/v1/models/{model.id}/star", headers=headers)

        assert response.status_code == 404, response.text
