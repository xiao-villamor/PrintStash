"""An administrator declares and withdraws a custom backup failure domain."""

import pytest


class TestStorageFailureDomains:
    @pytest.mark.asyncio
    async def test_manages_target_bound_independence_through_the_api(
        self, api, superuser_headers
    ) -> None:
        connected = await api.post(
            "/api/v1/storage-connections",
            headers=superuser_headers,
            json={
                "name": "Off-site archive",
                "kind": "s3",
                "purpose": "backup",
                "configuration": {
                    "provider": "s3_self_hosted",
                    "bucket": "backups",
                    "endpoint_url": "https://offsite.example.test",
                    "region": "us-east-1",
                    "root": "archive",
                    "addressing_style": "path",
                },
                "secrets": {
                    "access_key": "example-key",
                    "secret_key": "example-secret",
                },
            },
        )
        assert connected.status_code == 201, connected.text
        targets = await api.get("/api/v1/storage/targets", headers=superuser_headers)
        assert targets.status_code == 200, targets.text
        target = next(
            item for item in targets.json() if item["name"] == "Off-site archive"
        )
        assert target["evidence"] is None
        path = f"/api/v1/storage/targets/{target['target_ref']}/failure-domain"

        declared = await api.put(
            path, headers=superuser_headers, json={"failure_domain": "off-site"}
        )

        assert declared.status_code == 200, declared.text
        targets = await api.get("/api/v1/storage/targets", headers=superuser_headers)
        current = next(
            item
            for item in targets.json()
            if item["target_ref"] == target["target_ref"]
        )
        assert current["evidence"]["target"] == target["identity"]
        assert current["evidence"]["failure_domain"] == "administrator:off-site"
        removed = await api.delete(path, headers=superuser_headers)
        assert removed.status_code == 200, removed.text
        targets = await api.get("/api/v1/storage/targets", headers=superuser_headers)
        assert (
            next(
                item
                for item in targets.json()
                if item["target_ref"] == target["target_ref"]
            )["evidence"]
            is None
        )
