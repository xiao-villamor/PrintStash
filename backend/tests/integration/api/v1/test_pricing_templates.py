"""Pricing template persistence, unique names and route permissions."""

import pytest

TEMPLATES = "/api/v1/pricing-templates"


class TestPricingTemplates:
    def test_saves_named_pricing_rates(self, client, auth_headers):
        template = {"name": "Wholesale", "margin_percent": 20, "tax_percent": 10}
        created = client.post(TEMPLATES, headers=auth_headers, json=template)
        assert created.status_code == 201, created.text
        assert client.get(TEMPLATES, headers=auth_headers).json() == [created.json()]

    def test_rejects_duplicate_template_names(self, client, auth_headers):
        template = {"name": "Wholesale", "margin_percent": 20, "tax_percent": 10}
        client.post(TEMPLATES, headers=auth_headers, json=template)
        response = client.post(TEMPLATES, headers=auth_headers, json=template)
        assert response.status_code == 409

    def test_removes_a_pricing_template(self, client, auth_headers):
        template = {"name": "Wholesale", "margin_percent": 20, "tax_percent": 10}
        created = client.post(TEMPLATES, headers=auth_headers, json=template).json()
        assert (
            client.delete(
                f"{TEMPLATES}/{created['id']}", headers=auth_headers
            ).status_code
            == 204
        )
        assert client.get(TEMPLATES, headers=auth_headers).json() == []

    def test_restricts_template_writes_to_admins(self, client, user_headers):
        template = {"name": "Wholesale", "margin_percent": 20, "tax_percent": 10}
        assert (
            client.post(TEMPLATES, headers=user_headers(), json=template).status_code
            == 403
        )

    @pytest.mark.parametrize(
        "method,path",
        [("GET", TEMPLATES), ("POST", TEMPLATES), ("DELETE", f"{TEMPLATES}/unused")],
    )
    def test_template_access_requires_a_session(self, client, method, path):
        assert client.request(method, path).status_code == 401

    def test_only_admins_can_remove_templates(self, client, auth_headers, user_headers):
        created = client.post(
            TEMPLATES,
            headers=auth_headers,
            json={"name": "Wholesale", "margin_percent": 20, "tax_percent": 10},
        ).json()
        assert (
            client.delete(
                f"{TEMPLATES}/{created['id']}", headers=user_headers()
            ).status_code
            == 403
        )
        assert client.get(TEMPLATES, headers=auth_headers).json() == [created]
