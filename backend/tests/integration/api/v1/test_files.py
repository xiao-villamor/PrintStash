"""Saved costing follows Artifact ownership, versioned writes and template permissions."""

import pytest

from app.db.models import CollectionRole, FileType

EXPENSES = {
    "acquisition_cost": 500,
    "lifetime_hours": 1000,
    "lifetime_maintenance": 100,
    "power_watts": 200,
    "electricity_per_kwh": 0.25,
    "extra_per_hour": 1,
}
ESTIMATE = {
    "pieces": 2,
    "print_minutes": 120,
    "materials": [{"name": "PLA", "grams": 100, "cost_per_kg": 20, "spool_id": 7}],
    "printer": {"name": "Voron", "expenses": EXPENSES},
    "pricing": {"margin_percent": 20, "tax_percent": 10},
}


def _cost_url(artifact):
    return f"/api/v1/files/{artifact.id}/production-cost"


def _edit(headers, model_id, snapshot):
    epoch = snapshot["edit_epoch"]
    version = snapshot["edit_version"]
    return headers | {
        "If-Match": f'"model-{model_id}-e{epoch}-v{version}"',
        "X-PrintStash-Edit-Contract": "conditional-v1",
    }


class TestProductionCosting:
    @pytest.mark.parametrize("file_type", [FileType.STL, FileType.GCODE])
    def test_persists_a_snapshot_for_each_artifact(
        self, client, auth_headers, make_model, make_file, file_type
    ):
        model = make_model()
        artifact = make_file(model, file_type=file_type)
        sibling = make_file(model, file_type=FileType.STL)
        url = _cost_url(artifact)
        base = client.get(url, headers=auth_headers).json()
        assert base["estimate"] is None

        response = client.put(
            url,
            headers=_edit(auth_headers, model.id, base),
            json={"estimate": ESTIMATE},
        )

        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["estimate"] == ESTIMATE | {"currency": "USD"}
        assert saved["costs"]["production"] == 2.65
        assert saved["costs"]["final_price"] == 3.6438
        assert client.get(url, headers=auth_headers).json() == saved
        assert (
            client.get(_cost_url(sibling), headers=auth_headers).json()["estimate"]
            is None
        )

    def test_rejects_a_stale_write(self, client, auth_headers, make_model, make_file):
        model = make_model()
        artifact = make_file(model, file_type=FileType.STL)
        url = _cost_url(artifact)
        headers = _edit(
            auth_headers, model.id, client.get(url, headers=auth_headers).json()
        )

        saved = client.put(url, headers=headers, json={"estimate": ESTIMATE})
        stale = client.put(url, headers=headers, json={"estimate": None})

        assert saved.status_code == 200
        assert stale.status_code == 412
        assert client.get(url, headers=auth_headers).json() == saved.json()

    def test_clears_a_saved_estimate(self, client, auth_headers, make_model, make_file):
        model = make_model()
        url = _cost_url(make_file(model, file_type=FileType.STL))
        base = client.get(url, headers=auth_headers).json()
        saved = client.put(
            url,
            headers=_edit(auth_headers, model.id, base),
            json={"estimate": ESTIMATE},
        ).json()

        cleared = client.put(
            url, headers=_edit(auth_headers, model.id, saved), json={"estimate": None}
        )

        assert cleared.status_code == 200
        assert client.get(url, headers=auth_headers).json()["estimate"] is None

    @pytest.mark.parametrize(
        "payload,status",
        [
            ({"estimate": ESTIMATE}, 428),
            ({"estimate": ESTIMATE | {"pieces": 0}}, 422),
            (
                {
                    "estimate": ESTIMATE
                    | {"pricing": {"margin_percent": 100, "tax_percent": 0}}
                },
                422,
            ),
            ({"estimate": ESTIMATE | {"print_minutes": -1}}, 422),
            ({"estimate": ESTIMATE | {"materials": []}}, 422),
        ],
    )
    def test_rejects_invalid_costing_writes(
        self, client, auth_headers, make_model, make_file, payload, status
    ):
        url = _cost_url(make_file(make_model(), file_type=FileType.STL))

        assert client.put(url, headers=auth_headers, json=payload).status_code == status

    def test_hides_trashed_artifact_costs(
        self, client, auth_headers, make_model, make_file
    ):
        artifact = make_file(make_model(), file_type=FileType.STL, trashed=True)
        assert client.get(_cost_url(artifact), headers=auth_headers).status_code == 404

    def test_costing_follows_collection_permissions(
        self,
        client,
        make_model,
        make_file,
        make_collection,
        make_user,
        grant_role,
        headers_for,
    ):
        collection = make_collection()
        model = make_model(collection=collection)
        url = _cost_url(make_file(model, file_type=FileType.STL))
        user = make_user()
        headers = headers_for(user)
        assert client.get(url, headers=headers).status_code == 403
        grant_role(user, collection, CollectionRole.VIEW)
        base = client.get(url, headers=headers)
        assert base.status_code == 200
        assert (
            client.put(
                url,
                headers=_edit(headers, model.id, base.json()),
                json={"estimate": ESTIMATE},
            ).status_code
            == 403
        )

    @pytest.mark.parametrize("method", ["GET", "PUT"])
    def test_artifact_costing_requires_a_session(
        self, client, make_model, make_file, method
    ):
        url = _cost_url(make_file(make_model(), file_type=FileType.STL))
        assert (
            client.request(method, url, json={"estimate": ESTIMATE}).status_code == 401
        )

    def test_costing_write_requires_write_scope(
        self, client, make_user, headers_for, make_model, make_file
    ):
        url = _cost_url(make_file(make_model(), file_type=FileType.STL))
        headers = headers_for(make_user(superuser=True), scope="read")
        base = client.get(url, headers=headers)
        assert base.status_code == 200
        assert (
            client.put(url, headers=headers, json={"estimate": ESTIMATE}).status_code
            == 401
        )

    def test_costing_does_not_relabel_saved_currency(
        self, client, auth_headers, make_model, make_file, db_session
    ):
        from app.modules.administration.runtime_config import set_currency

        model = make_model()
        url = _cost_url(make_file(model, file_type=FileType.STL))
        base = client.get(url, headers=auth_headers).json()
        saved = client.put(
            url,
            headers=_edit(auth_headers, model.id, base),
            json={"estimate": ESTIMATE},
        ).json()

        set_currency(db_session, "EUR")
        response = client.put(
            url,
            headers=_edit(auth_headers, model.id, saved),
            json={"estimate": ESTIMATE},
        )

        assert response.status_code == 200, response.text
        assert response.json()["currency"] == "EUR"
        assert response.json()["estimate"]["currency"] == "USD"
