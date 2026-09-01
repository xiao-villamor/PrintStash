"""Part Groups expose source Artifacts as mutually exclusive choices."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import CollectionRole, FileType, PartGroup, PartOption
from tests.factories import (
    bearer,
    build_collection,
    build_file,
    build_model,
    build_user,
    grant_collection_role,
)


def _payload(first_id: int, second_id: int) -> dict:
    return {
        "groups": [
            {
                "name": " Handle ",
                "options": [
                    {"file_id": first_id, "name": "Short", "is_default": True},
                    {"file_id": second_id, "name": "Long", "is_default": False},
                ],
            }
        ]
    }


class TestReplacePartOptions:
    def test_replaces_the_complete_part_option_set(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Configurable handle")
        short = build_file(db_session, model, filename="handle-short.stl")
        long = build_file(db_session, model, filename="handle-long.stl")

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=_payload(short.id, long.id),
            headers=auth_headers,
        )

        assert response.status_code == 200, response.text
        assert response.json()["part_groups"] == [
            {
                "id": response.json()["part_groups"][0]["id"],
                "name": "Handle",
                "options": [
                    {
                        "id": response.json()["part_groups"][0]["options"][0]["id"],
                        "file_id": short.id,
                        "name": "Short",
                        "is_default": True,
                    },
                    {
                        "id": response.json()["part_groups"][0]["options"][1]["id"],
                        "file_id": long.id,
                        "name": "Long",
                        "is_default": False,
                    },
                ],
            }
        ]

    def test_rejects_one_artifact_in_multiple_groups_without_partial_writes(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Atomic choices")
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        third = build_file(db_session, model, filename="third.stl")
        assert (
            client.put(
                f"/api/v1/models/{model.id}/part-options",
                json=_payload(first.id, second.id),
                headers=auth_headers,
            ).status_code
            == 200
        )

        invalid = {
            "groups": [
                _payload(first.id, second.id)["groups"][0],
                {
                    "name": "Lid",
                    "options": [
                        {"file_id": first.id, "name": "Open", "is_default": True},
                        {"file_id": third.id, "name": "Closed"},
                    ],
                },
            ]
        }
        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=invalid,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "artifact_already_part_option"
        assert len(db_session.exec(select(PartGroup)).all()) == 1
        assert len(db_session.exec(select(PartOption)).all()) == 2

    def test_rejects_gcode_revisions(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Revision is not a choice")
        mesh = build_file(db_session, model, filename="part.stl")
        gcode = build_file(
            db_session,
            model,
            filename="part.gcode",
            file_type=FileType.GCODE,
        )

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=_payload(mesh.id, gcode.id),
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "part_option_artifact_not_source"

    def test_rejects_duplicate_group_names(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Duplicate groups")
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        group = _payload(first.id, second.id)["groups"][0]

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json={"groups": [group, {**group, "name": "handle"}]},
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "part_group_name_duplicate"

    def test_rejects_duplicate_option_names(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Duplicate choices")
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        payload = _payload(first.id, second.id)
        payload["groups"][0]["options"][1]["name"] = " short "

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "part_option_name_duplicate"

    def test_requires_exactly_one_default(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "No default")
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        payload = _payload(first.id, second.id)
        payload["groups"][0]["options"][0]["is_default"] = False

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=payload,
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "part_group_default_required"

    def test_rejects_an_artifact_outside_the_model(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Local choices")
        other = build_model(db_session, "Foreign choices")
        first = build_file(db_session, model, filename="first.stl")
        foreign = build_file(db_session, other, filename="foreign.stl")

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=_payload(first.id, foreign.id),
            headers=auth_headers,
        )

        assert response.status_code == 400
        assert response.json()["detail"] == "part_option_artifact_not_found"

    def test_requires_effective_edit_access(
        self, client: TestClient, db_session: Session
    ) -> None:
        collection = build_collection(db_session, "Protected choices")
        model = build_model(db_session, "Protected", collection=collection)
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        viewer = build_user(db_session, "part-options-viewer")
        grant_collection_role(db_session, viewer, collection, CollectionRole.VIEW)

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json=_payload(first.id, second.id),
            headers=bearer(viewer),
        )

        assert response.status_code == 403
        assert db_session.exec(select(PartGroup)).all() == []

    def test_empty_set_clears_existing_groups(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Clear choices")
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        assert (
            client.put(
                f"/api/v1/models/{model.id}/part-options",
                json=_payload(first.id, second.id),
                headers=auth_headers,
            ).status_code
            == 200
        )

        response = client.put(
            f"/api/v1/models/{model.id}/part-options",
            json={"groups": []},
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert response.json()["part_groups"] == []
        assert db_session.exec(select(PartGroup)).all() == []

    def test_trashed_choice_hides_the_incomplete_group_until_restore(
        self, client: TestClient, db_session: Session, auth_headers: dict[str, str]
    ) -> None:
        model = build_model(db_session, "Restorable choices")
        first = build_file(db_session, model, filename="first.stl")
        second = build_file(db_session, model, filename="second.stl")
        assert (
            client.put(
                f"/api/v1/models/{model.id}/part-options",
                json=_payload(first.id, second.id),
                headers=auth_headers,
            ).status_code
            == 200
        )

        first.deleted_at = utcnow()
        db_session.add(first)
        db_session.commit()
        assert (
            client.get(f"/api/v1/models/{model.id}", headers=auth_headers).json()[
                "part_groups"
            ]
            == []
        )

        first.deleted_at = None
        db_session.add(first)
        db_session.commit()
        restored = client.get(f"/api/v1/models/{model.id}", headers=auth_headers)
        assert restored.json()["part_groups"][0]["name"] == "Handle"
