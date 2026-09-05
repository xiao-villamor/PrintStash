"""A physical build retains its requirements and every manufacturing attempt."""

import pytest
from sqlmodel import select

from app.db.models import FileType, PrintJob, PrintJobState


@pytest.fixture
def composition(client, auth_headers, make_model, make_file):
    model = make_model("Table leg")
    revision = make_file(model, file_type=FileType.GCODE, filename="one-leg.gcode")
    created = client.post(
        "/api/v1/multipart-models", headers=auth_headers, json={"name": "Table"}
    )
    assert created.status_code == 201
    saved = client.put(
        f"/api/v1/multipart-models/{created.json()['id']}",
        headers=auth_headers,
        json={
            "parts": [
                {"name": "Leg", "quantity": 4, "choices": [{"model_id": model.id}]}
            ]
        },
    )
    assert saved.status_code == 200, saved.text
    return saved.json(), revision


@pytest.fixture
def build(client, auth_headers, composition):
    assembly, revision = composition
    response = client.post(
        "/api/v1/multipart-builds",
        headers=auth_headers,
        json={
            "multipart_model_id": assembly["id"],
            "name": "Kitchen table",
            "object_quantity": 1,
        },
    )
    assert response.status_code == 201, response.text
    return response.json(), revision


@pytest.fixture
def queued_build(client, auth_headers, build):
    data, revision = build
    part_id = data["parts"][0]["id"]
    selected = client.patch(
        f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}",
        headers=auth_headers,
        json={"version": data["version"], "revision_id": revision.id},
    )
    assert selected.status_code == 200
    queued = client.post(
        f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}/queue",
        headers=auth_headers,
        json={"version": selected.json()["version"], "units_per_job": 4},
    )
    assert queued.status_code == 201, queued.text
    return queued.json()


@pytest.fixture
def finished_build(queued_build, db_session):
    attempt = queued_build["parts"][0]["attempts"][0]
    job = db_session.get(PrintJob, attempt["job_id"])
    job.state = PrintJobState.COMPLETED
    db_session.add(job)
    db_session.commit()
    return queued_build


class TestMultipartBuilds:
    def test_part_quantity_survives_composition_save(self, composition):
        assert composition[0]["parts"][0]["quantity"] == 4

    def test_build_snapshots_required_units(self, build):
        data, _ = build
        assert data["parts"][0]["required_units"] == 4
        assert data["parts"][0]["missing_units"] == 4

    def test_build_without_revision_remains_a_draft(self, build):
        data, _ = build
        assert data["parts"][0]["revision_id"] is None
        assert data["parts"][0]["queueable"] is False

    def test_queue_requires_a_selected_revision(self, client, auth_headers, build):
        data, _ = build
        response = client.post(
            f"/api/v1/multipart-builds/{data['id']}/parts/{data['parts'][0]['id']}/queue",
            headers=auth_headers,
            json={"version": data["version"], "units_per_job": 1},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_revision_required"

    def test_revision_must_belong_to_the_selected_model(
        self, client, auth_headers, build, make_model, make_file
    ):
        data, _ = build
        other = make_file(make_model("Unrelated"), file_type=FileType.GCODE)
        response = client.patch(
            f"/api/v1/multipart-builds/{data['id']}/parts/{data['parts'][0]['id']}",
            headers=auth_headers,
            json={"version": data["version"], "revision_id": other.id},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "build_revision_invalid"

    def test_partial_confirmation_leaves_one_unit_missing(
        self, client, auth_headers, build, db_session
    ):
        data, revision = build
        part_id = data["parts"][0]["id"]
        selected = client.patch(
            f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}",
            headers=auth_headers,
            json={"version": data["version"], "revision_id": revision.id},
        ).json()
        queued = client.post(
            f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}/queue",
            headers=auth_headers,
            json={"version": selected["version"], "units_per_job": 4},
        )
        assert queued.status_code == 201, queued.text
        attempt = queued.json()["parts"][0]["attempts"][0]
        job = db_session.get(PrintJob, attempt["job_id"])
        job.state = PrintJobState.FAILED
        db_session.add(job)
        db_session.commit()
        result = client.post(
            f"/api/v1/multipart-builds/{data['id']}/attempts/{attempt['id']}/confirm",
            headers=auth_headers,
            json={"version": 0, "idempotency_key": "partial-result", "valid_units": 3},
        )
        assert result.status_code == 200, result.text
        assert result.json()["parts"][0]["missing_units"] == 1
        assert db_session.exec(select(PrintJob)).one().state == PrintJobState.FAILED

    def test_active_jobs_reserve_planned_units(self, queued_build):
        part = queued_build["parts"][0]
        assert part["active_units"] == 4
        assert part["unreserved_units"] == 0
        assert part["missing_units"] == 4

    def test_completed_jobs_need_explicit_confirmation(
        self, client, auth_headers, finished_build
    ):
        response = client.get(
            f"/api/v1/multipart-builds/{finished_build['id']}", headers=auth_headers
        )
        part = response.json()["parts"][0]
        assert part["valid_units"] == 0
        assert part["unreviewed_units"] == 4
        assert part["attempts"][0]["suggested_valid_units"] == 4
        assert part["unreserved_units"] == 0

    def test_confirmed_output_completes_the_build(
        self, client, auth_headers, finished_build
    ):
        attempt = finished_build["parts"][0]["attempts"][0]
        response = client.post(
            f"/api/v1/multipart-builds/{finished_build['id']}/attempts/{attempt['id']}/confirm",
            headers=auth_headers,
            json={"version": 0, "valid_units": 4, "idempotency_key": "finished"},
        )
        assert response.status_code == 200
        assert response.json()["completed"] is True

    def test_repeated_confirmation_does_not_count_twice(
        self, client, auth_headers, finished_build
    ):
        attempt = finished_build["parts"][0]["attempts"][0]
        url = f"/api/v1/multipart-builds/{finished_build['id']}/attempts/{attempt['id']}/confirm"
        payload = {"version": 0, "valid_units": 3, "idempotency_key": "same-result"}
        first = client.post(url, headers=auth_headers, json=payload)
        second = client.post(url, headers=auth_headers, json=payload)
        assert first.status_code == second.status_code == 200
        assert second.json()["parts"][0]["valid_units"] == 3
        assert second.json()["version"] == first.json()["version"]

    def test_stale_result_cannot_overwrite_a_confirmation(
        self, client, auth_headers, finished_build
    ):
        attempt = finished_build["parts"][0]["attempts"][0]
        url = f"/api/v1/multipart-builds/{finished_build['id']}/attempts/{attempt['id']}/confirm"
        assert (
            client.post(
                url,
                headers=auth_headers,
                json={"version": 0, "valid_units": 3, "idempotency_key": "first"},
            ).status_code
            == 200
        )
        stale = client.post(
            url,
            headers=auth_headers,
            json={"version": 0, "valid_units": 4, "idempotency_key": "stale"},
        )
        assert stale.status_code == 409
        assert stale.json()["detail"] == "build_result_version_conflict"

    def test_replaying_an_old_receipt_preserves_a_later_correction(
        self, client, auth_headers, finished_build
    ):
        attempt = finished_build["parts"][0]["attempts"][0]
        url = f"/api/v1/multipart-builds/{finished_build['id']}/attempts/{attempt['id']}/confirm"
        first = {"version": 0, "valid_units": 3, "idempotency_key": "first"}
        assert client.post(url, headers=auth_headers, json=first).status_code == 200
        assert (
            client.post(
                url,
                headers=auth_headers,
                json={"version": 1, "valid_units": 2, "idempotency_key": "correction"},
            ).status_code
            == 200
        )
        replay = client.post(url, headers=auth_headers, json=first)
        assert replay.status_code == 200
        assert replay.json()["parts"][0]["valid_units"] == 2

    def test_a_reprint_preserves_the_failed_attempt(
        self, client, auth_headers, queued_build, db_session
    ):
        part = queued_build["parts"][0]
        attempt = part["attempts"][0]
        job = db_session.get(PrintJob, attempt["job_id"])
        job.state = PrintJobState.FAILED
        db_session.add(job)
        db_session.commit()
        result = client.post(
            f"/api/v1/multipart-builds/{queued_build['id']}/attempts/{attempt['id']}/confirm",
            headers=auth_headers,
            json={"version": 0, "valid_units": 3, "idempotency_key": "partial"},
        ).json()
        reprint = client.post(
            f"/api/v1/multipart-builds/{queued_build['id']}/parts/{part['id']}/queue",
            headers=auth_headers,
            json={"version": result["version"], "units_per_job": 1},
        )
        assert reprint.status_code == 201, reprint.text
        attempts = reprint.json()["parts"][0]["attempts"]
        assert len(attempts) == 2
        assert attempts[0]["state"] == "failed"
        assert attempts[0]["valid_units"] == 3
        assert attempts[1]["job_id"] != attempts[0]["job_id"]

    def test_generic_retry_cannot_reset_a_physical_attempt(
        self, client, auth_headers, queued_build, db_session
    ):
        job_id = queued_build["parts"][0]["attempts"][0]["job_id"]
        job = db_session.get(PrintJob, job_id)
        job.state = PrintJobState.FAILED
        job.retryable = True
        db_session.add(job)
        db_session.commit()
        response = client.post(
            f"/api/v1/fleet/queue/{job_id}/retry", headers=auth_headers
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"] == "build_reprint_requires_new_job"

    def test_duplicate_starts_without_results(
        self, client, auth_headers, finished_build
    ):
        response = client.post(
            f"/api/v1/multipart-builds/{finished_build['id']}/duplicate",
            headers=auth_headers,
            json={"name": "Second table"},
        )
        assert response.status_code == 201
        assert response.json()["parts"][0]["attempts"] == []
        assert response.json()["parts"][0]["missing_units"] == 4
        assert response.json()["id"] != finished_build["id"]

    def test_archived_build_cannot_enqueue_more_jobs(
        self, client, auth_headers, queued_build
    ):
        build_id = queued_build["id"]
        archived = client.patch(
            f"/api/v1/multipart-builds/{build_id}/archive",
            headers=auth_headers,
            json={"version": queued_build["version"]},
        )
        assert archived.status_code == 200
        response = client.post(
            f"/api/v1/multipart-builds/{build_id}/parts/{queued_build['parts'][0]['id']}/queue",
            headers=auth_headers,
            json={"version": archived.json()["version"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_archived"

    def test_composition_deletion_keeps_manufacturing_history(
        self, client, auth_headers, queued_build
    ):
        response = client.delete(
            f"/api/v1/multipart-models/{queued_build['multipart_model_id']}",
            headers=auth_headers,
        )
        assert response.status_code == 204
        read = client.get(
            f"/api/v1/multipart-builds/{queued_build['id']}", headers=auth_headers
        )
        assert read.status_code == 200
        assert read.json()["composition_name"] == "Table"
        assert read.json()["parts"][0]["required_units"] == 4
        assert len(read.json()["parts"][0]["attempts"]) == 1

    @pytest.mark.parametrize("quantity", [0, -1, 10_001])
    def test_rejects_invalid_object_quantities(
        self, client, auth_headers, composition, quantity
    ):
        response = client.post(
            "/api/v1/multipart-builds",
            headers=auth_headers,
            json={
                "multipart_model_id": composition[0]["id"],
                "name": "Invalid",
                "object_quantity": quantity,
            },
        )
        assert response.status_code == 422

    def test_history_requires_collection_access(self, client, user_headers, build):
        response = client.get(
            f"/api/v1/multipart-builds/{build[0]['id']}", headers=user_headers()
        )
        assert response.status_code == 403

    def test_read_scope_cannot_create_a_build(self, client, user_headers, composition):
        response = client.post(
            "/api/v1/multipart-builds",
            headers=user_headers(is_superuser=True, scope="read"),
            json={"multipart_model_id": composition[0]["id"], "name": "Unauthorized"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "insufficient_scope"

    def test_excess_output_requires_confirmation(self, client, auth_headers, build):
        data, revision = build
        part_id = data["parts"][0]["id"]
        selected = client.patch(
            f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}",
            headers=auth_headers,
            json={"version": data["version"], "revision_id": revision.id},
        ).json()
        response = client.post(
            f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}/queue",
            headers=auth_headers,
            json={"version": selected["version"], "units_per_job": 3},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_excess_confirmation_required"
        accepted = client.post(
            f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}/queue",
            headers=auth_headers,
            json={
                "version": selected["version"],
                "units_per_job": 3,
                "confirm_excess": True,
            },
        )
        assert accepted.status_code == 201
        assert accepted.json()["parts"][0]["active_units"] == 6

    def test_failed_link_creation_rolls_back_queued_jobs(
        self, client, auth_headers, build, monkeypatch, db_session
    ):
        from sqlmodel import Session

        from app.db.models import MultipartBuildAttempt

        data, revision = build
        part_id = data["parts"][0]["id"]
        selected = client.patch(
            f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}",
            headers=auth_headers,
            json={"version": data["version"], "revision_id": revision.id},
        ).json()
        original = Session.add

        def fail_link(self, row, **kwargs):
            if isinstance(row, MultipartBuildAttempt):
                raise RuntimeError("injected link write failure")
            return original(self, row, **kwargs)

        monkeypatch.setattr(Session, "add", fail_link)
        with pytest.raises(RuntimeError, match="injected link write failure"):
            client.post(
                f"/api/v1/multipart-builds/{data['id']}/parts/{part_id}/queue",
                headers=auth_headers,
                json={"version": selected["version"], "units_per_job": 4},
            )
        db_session.expire_all()
        assert db_session.exec(select(PrintJob)).all() == []


class TestManufacturingRecoveryAndValidation:
    def test_list_filters_archives_and_composition(self, client, auth_headers, build):
        data, _ = build
        base = "/api/v1/multipart-builds"
        listed = client.get(
            base,
            headers=auth_headers,
            params={"multipart_model_id": data["multipart_model_id"]},
        )
        assert [item["id"] for item in listed.json()] == [data["id"]]
        assert (
            client.get(
                base, headers=auth_headers, params={"multipart_model_id": 999999}
            ).json()
            == []
        )
        archived = client.patch(
            f"{base}/{data['id']}/archive",
            headers=auth_headers,
            json={"version": data["version"], "archived": True},
        )
        assert archived.status_code == 200
        assert client.get(base, headers=auth_headers).json() == []
        assert (
            client.get(base, headers=auth_headers, params={"archived": True}).json()[0][
                "id"
            ]
            == data["id"]
        )
        stale = client.patch(
            f"{base}/{data['id']}/archive",
            headers=auth_headers,
            json={"version": data["version"], "archived": False},
        )
        assert stale.status_code == 409
        restored = client.patch(
            f"{base}/{data['id']}/archive",
            headers=auth_headers,
            json={"version": archived.json()["version"], "archived": False},
        )
        assert restored.status_code == 200
        assert restored.json()["archived_at"] is None

    def test_private_build_is_absent_from_unauthorized_list(
        self, client, user_headers, build
    ):
        assert (
            client.get("/api/v1/multipart-builds", headers=user_headers()).json() == []
        )

    def test_missing_build_and_foreign_part_are_not_found(
        self, client, auth_headers, build
    ):
        data, revision = build
        assert (
            client.get(
                "/api/v1/multipart-builds/999999", headers=auth_headers
            ).status_code
            == 404
        )
        response = client.patch(
            f"/api/v1/multipart-builds/{data['id']}/parts/999999",
            headers=auth_headers,
            json={"version": data["version"], "revision_id": revision.id},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "build_part_not_found"

    def test_empty_composition_cannot_create_build(self, client, auth_headers):
        composition = client.post(
            "/api/v1/multipart-models", headers=auth_headers, json={"name": "Empty"}
        ).json()
        response = client.post(
            "/api/v1/multipart-builds",
            headers=auth_headers,
            json={"multipart_model_id": composition["id"], "name": "Empty build"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "build_composition_empty"

    @pytest.mark.parametrize("duplicate", [False, True])
    def test_whitespace_name_is_rejected(self, client, auth_headers, build, duplicate):
        data, _ = build
        base = "/api/v1/multipart-builds"
        payload = {"name": "   "}
        if not duplicate:
            payload["multipart_model_id"] = data["multipart_model_id"]
        url = f"{base}/{data['id']}/duplicate" if duplicate else base
        response = client.post(url, headers=auth_headers, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == "build_name_empty"

    @pytest.mark.parametrize("selection", [{"choice_id": 999999}, {"model_id": 999999}])
    def test_selection_must_be_in_historical_choices(
        self, client, auth_headers, build, selection
    ):
        data, _ = build
        response = client.patch(
            f"/api/v1/multipart-builds/{data['id']}/parts/{data['parts'][0]['id']}",
            headers=auth_headers,
            json={"version": data["version"], **selection},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "build_choice_invalid"

    def test_stale_selection_cannot_overwrite_revision(
        self, client, auth_headers, build
    ):
        data, revision = build
        url = f"/api/v1/multipart-builds/{data['id']}/parts/{data['parts'][0]['id']}"
        payload = {
            "version": data["version"],
            "revision_id": revision.id,
            "choice_id": data["parts"][0]["selected_choice_id"],
        }
        assert client.patch(url, headers=auth_headers, json=payload).status_code == 200
        response = client.patch(
            url, headers=auth_headers, json={**payload, "revision_id": None}
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_version_conflict"
        assert (
            client.get(
                f"/api/v1/multipart-builds/{data['id']}", headers=auth_headers
            ).json()["parts"][0]["revision_id"]
            == revision.id
        )

    def test_reserved_units_cannot_be_enqueued_twice(
        self, client, auth_headers, queued_build
    ):
        data = queued_build
        response = client.post(
            f"/api/v1/multipart-builds/{data['id']}/parts/{data['parts'][0]['id']}/queue",
            headers=auth_headers,
            json={"version": data["version"]},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_no_unreserved_units"

    @pytest.mark.parametrize(
        "units, expected",
        [(5, "build_valid_units_exceed_planned"), (1, "build_job_not_finished")],
    )
    def test_invalid_result_does_not_change_output(
        self, client, auth_headers, queued_build, units, expected
    ):
        data = queued_build
        attempt = data["parts"][0]["attempts"][0]
        response = client.post(
            f"/api/v1/multipart-builds/{data['id']}/attempts/{attempt['id']}/confirm",
            headers=auth_headers,
            json={"version": 0, "valid_units": units, "idempotency_key": "invalid"},
        )
        assert response.status_code in (400, 409)
        assert response.json()["detail"] == expected
        part = client.get(
            f"/api/v1/multipart-builds/{data['id']}", headers=auth_headers
        ).json()["parts"][0]
        assert part["valid_units"] == 0
        assert part["attempts"][0]["version"] == 0

    def test_confirmation_key_cannot_be_reused_for_different_output(
        self, client, auth_headers, finished_build
    ):
        data = finished_build
        attempt = data["parts"][0]["attempts"][0]
        url = f"/api/v1/multipart-builds/{data['id']}/attempts/{attempt['id']}/confirm"
        payload = {"version": 0, "valid_units": 3, "idempotency_key": "fixed-result"}
        assert client.post(url, headers=auth_headers, json=payload).status_code == 200
        response = client.post(
            url, headers=auth_headers, json={**payload, "valid_units": 4}
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_idempotency_conflict"

    def test_unknown_attempt_is_not_found(self, client, auth_headers, build):
        data, _ = build
        response = client.post(
            f"/api/v1/multipart-builds/{data['id']}/attempts/999999/confirm",
            headers=auth_headers,
            json={"version": 0, "valid_units": 0, "idempotency_key": "missing"},
        )
        assert response.status_code == 404
        assert response.json()["detail"] == "build_attempt_not_found"

    def test_archived_build_rejects_new_confirmation(
        self, client, auth_headers, finished_build
    ):
        data = finished_build
        attempt = data["parts"][0]["attempts"][0]
        assert (
            client.patch(
                f"/api/v1/multipart-builds/{data['id']}/archive",
                headers=auth_headers,
                json={"version": data["version"], "archived": True},
            ).status_code
            == 200
        )
        response = client.post(
            f"/api/v1/multipart-builds/{data['id']}/attempts/{attempt['id']}/confirm",
            headers=auth_headers,
            json={"version": 0, "valid_units": 3, "idempotency_key": "archived"},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "build_archived"
