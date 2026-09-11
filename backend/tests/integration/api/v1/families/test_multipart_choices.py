"""Family selection supplies Model IDs; the Multipart owner rechecks their access."""

from app.db.models import CollectionRole


class TestFamilyChoices:
    def test_rejects_invisible_sibling_choice(
        self,
        client,
        auth_headers,
        db_session,
        make_user,
        headers_for,
        make_collection,
        grant_role,
        make_model,
        make_family,
        make_family_member,
    ):
        actor = make_user()
        shared, private = make_collection("Workshop"), make_collection("Variants")
        grant_role(actor, shared, CollectionRole.EDIT)
        permission = grant_role(actor, private, CollectionRole.VIEW)
        original = make_model("Original", collection=shared)
        sibling = make_model("Variant", collection=private)
        family = make_family()
        make_family_member(family, original, canonical=True)
        make_family_member(family, sibling)
        headers = headers_for(actor)
        candidates = client.get(
            f"/api/v1/families/{family.id}/members", headers=headers
        )
        assert candidates.status_code == 200, candidates.text
        assert {member["model_id"] for member in candidates.json()["items"]} == {
            original.id,
            sibling.id,
        }
        aggregate = client.post(
            "/api/v1/multipart-models",
            headers=auth_headers,
            json={
                "name": "Kit",
                "collection_id": shared.id,
            },
        ).json()
        payload = {
            "parts": [{"name": "Option", "choices": [{"model_id": original.id}]}]
        }
        initial = client.put(
            f"/api/v1/multipart-models/{aggregate['id']}/parts",
            headers=headers,
            json=payload,
        )
        assert initial.status_code == 200, initial.text
        db_session.delete(permission)
        db_session.commit()
        payload["parts"][0]["choices"].append({"model_id": sibling.id})
        denied = client.put(
            f"/api/v1/multipart-models/{aggregate['id']}/parts",
            headers=headers,
            json=payload,
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"] == "collection_permission_denied"
        unchanged = client.get(
            f"/api/v1/multipart-models/{aggregate['id']}", headers=headers
        ).json()
        assert [model["id"] for model in unchanged["parts"][0]["models"]] == [
            original.id
        ]
