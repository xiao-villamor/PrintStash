"""Only administrators can bind independence evidence to current targets."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app.db.models import StorageFailureDomainDeclaration
from tests.factories import bearer, build_user


@pytest.fixture
def custom_backup_target(client: TestClient, db_session: Session):
    headers = bearer(
        build_user(db_session, "domain-admin", superuser=True), scope="admin"
    )
    response = client.post(
        "/api/v1/storage-connections",
        headers=headers,
        json={
            "name": "Independent replica",
            "kind": "s3",
            "purpose": "backup",
            "configuration": {
                "provider": "s3_self_hosted",
                "bucket": "backup",
                "endpoint_url": "https://offsite.example.test",
                "region": "us-east-1",
                "root": "archive",
                "addressing_style": "path",
            },
            "secrets": {"access_key": "example-key", "secret_key": "example-secret"},
        },
    )
    assert response.status_code == 201, response.text
    targets = client.get("/api/v1/storage/targets", headers=headers)
    assert targets.status_code == 200, targets.text
    assert "example-secret" not in targets.text
    target = next(
        item for item in targets.json() if item["name"] == "Independent replica"
    )
    return headers, target


class TestFailureDomainAdministration:
    def test_declaration_is_bound_to_the_selected_target(
        self, client: TestClient, db_session: Session, custom_backup_target
    ) -> None:
        headers, target = custom_backup_target
        path = f"/api/v1/storage/targets/{target['target_ref']}/failure-domain"
        assert target["evidence"] is None

        declared = client.put(
            path, headers=headers, json={"failure_domain": "off-site"}
        )

        assert declared.status_code == 200, declared.text
        db_session.expire_all()
        row = db_session.get(StorageFailureDomainDeclaration, target["target_ref"])
        assert row is not None and row.failure_domain == "off-site"
        current = client.get("/api/v1/storage/targets", headers=headers).json()
        evidence = next(
            item["evidence"]
            for item in current
            if item["target_ref"] == target["target_ref"]
        )
        assert evidence["target"] == target["identity"]
        assert evidence["declaration_revision"] == declared.json()["revision"]

    def test_editing_a_declaration_changes_its_revision(
        self, client: TestClient, custom_backup_target
    ) -> None:
        headers, target = custom_backup_target
        path = f"/api/v1/storage/targets/{target['target_ref']}/failure-domain"
        first = client.put(path, headers=headers, json={"failure_domain": "off-site"})

        changed = client.put(
            path, headers=headers, json={"failure_domain": "shared-site"}
        )

        assert changed.status_code == 200, changed.text
        assert changed.json()["failure_domain"] == "shared-site"
        assert changed.json()["revision"] != first.json()["revision"]

    def test_withdrawal_is_idempotent(
        self, client: TestClient, custom_backup_target
    ) -> None:
        headers, target = custom_backup_target
        path = f"/api/v1/storage/targets/{target['target_ref']}/failure-domain"
        assert (
            client.put(
                path, headers=headers, json={"failure_domain": "off-site"}
            ).status_code
            == 200
        )

        assert client.delete(path, headers=headers).status_code == 200
        assert client.delete(path, headers=headers).status_code == 200
        current = client.get("/api/v1/storage/targets", headers=headers).json()
        assert (
            next(
                item for item in current if item["target_ref"] == target["target_ref"]
            )["evidence"]
            is None
        )

    @pytest.mark.parametrize(
        "method,path,body",
        [
            ("get", "/api/v1/storage/targets", None),
            (
                "put",
                "/api/v1/storage/targets/unknown/failure-domain",
                {"failure_domain": "off-site"},
            ),
            ("delete", "/api/v1/storage/targets/unknown/failure-domain", None),
        ],
    )
    def test_members_cannot_administer_failure_domains(
        self,
        client: TestClient,
        db_session: Session,
        method: str,
        path: str,
        body: dict | None,
    ) -> None:
        headers = bearer(build_user(db_session, "domain-member"))

        response = client.request(method, path, headers=headers, json=body)

        assert response.status_code == 403
        assert not db_session.exec(select(StorageFailureDomainDeclaration)).all()

    def test_rejects_a_stale_target_reference(
        self, client: TestClient, custom_backup_target
    ) -> None:
        headers, _ = custom_backup_target

        response = client.put(
            "/api/v1/storage/targets/stale/failure-domain",
            headers=headers,
            json={"failure_domain": "off-site"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "storage_target_changed"

    def test_cannot_override_a_provider_domain(
        self, client: TestClient, custom_backup_target
    ) -> None:
        headers, _ = custom_backup_target
        targets = client.get("/api/v1/storage/targets", headers=headers).json()
        vault = next(item for item in targets if item["role"] == "vault")

        response = client.put(
            f"/api/v1/storage/targets/{vault['target_ref']}/failure-domain",
            headers=headers,
            json={"failure_domain": "different"},
        )

        assert response.status_code == 409
        assert response.json()["detail"] == "storage_failure_domain_provider_defined"
