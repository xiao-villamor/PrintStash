"""The Unraid catalog exposes one unified app and retains deprecated legacy paths."""

from __future__ import annotations

from xml.etree import ElementTree

import pytest

from tests.paths import REPO_ROOT


def _template() -> ElementTree.Element:
    return ElementTree.parse(REPO_ROOT / "templates/printstash.xml").getroot()


def _configs(kind: str) -> list[ElementTree.Element]:
    return _template().findall(f"Config[@Type='{kind}']")


class TestPrintStashTemplate:
    def test_selects_the_unified_image(self) -> None:
        template = _template()

        assert template.findtext("Name") == "PrintStash"
        assert (
            template.findtext("Repository") == "ghcr.io/xiao-villamor/printstash:latest"
        )
        assert template.findtext("Network") == "bridge"
        assert template.findtext("TemplateURL", "").endswith(
            "/templates/printstash.xml"
        )
        assert not template.findtext("PostArgs")

    def test_exposes_only_the_web_port(self) -> None:
        assert [(item.get("Target"), item.text) for item in _configs("Port")] == [
            ("3000", "3000")
        ]
        assert _template().findtext("WebUI") == "http://[IP]:[PORT:3000]/"

    def test_persists_the_data_parent(self) -> None:
        assert [
            (item.get("Target"), item.get("Default"), item.get("Mode"), item.text)
            for item in _configs("Path")
        ] == [
            (
                "/data",
                "/mnt/user/appdata/printstash",
                "rw",
                "/mnt/user/appdata/printstash",
            )
        ]

    def test_enables_first_run_without_a_blank_secret(self) -> None:
        variables = {item.get("Target"): item for item in _configs("Variable")}

        assert variables["VAULT_SETUP_MODE"].text == "trusted_network"
        assert "VAULT_JWT_SECRET" not in variables

    def test_uses_unraid_file_identity(self) -> None:
        variables = {item.get("Target"): item for item in _configs("Variable")}

        assert variables["PUID"].text == "99"
        assert variables["PGID"].text == "100"

    def test_restarts_the_supervised_container(self) -> None:
        variables = {item.get("Target"): item for item in _configs("Variable")}

        assert variables["VAULT_RESTART_ENABLED"].text == "true"
        assert _template().findtext("ExtraParams") == "--restart=unless-stopped"


class TestCommunityApplicationsProfile:
    def test_community_applications_profile_describes_one_container(self) -> None:
        profile = ElementTree.parse(REPO_ROOT / "ca_profile.xml").getroot()
        description = profile.findtext("Profile", "")

        assert "one container" in description
        assert "two-container users" in description
        assert "Install **PrintStash-API first**" not in description


class TestTemplateCatalog:
    def test_only_one_current_catalog_template(self) -> None:
        templates = [
            ElementTree.parse(path).getroot()
            for path in sorted((REPO_ROOT / "templates").glob("printstash*.xml"))
        ]
        current = [item for item in templates if item.findtext("Deprecated") != "true"]

        assert len(current) == 1
        assert current[0].findtext("Name") == "PrintStash"

    @pytest.mark.parametrize(
        ("filename", "name", "image"),
        [
            ("printstash-api.xml", "PrintStash-API", "printstash-api"),
            ("printstash-frontend.xml", "PrintStash-Frontend", "printstash-frontend"),
        ],
        ids=["api", "frontend"],
    )
    def test_legacy_catalog_templates_are_deprecated(
        self, filename: str, name: str, image: str
    ) -> None:
        template = ElementTree.parse(REPO_ROOT / "templates" / filename).getroot()

        assert template.findtext("Deprecated") == "true"
        assert template.findtext("Name") == name
        assert template.findtext("Repository") == (
            f"ghcr.io/xiao-villamor/{image}:latest"
        )
        assert template.findtext("TemplateURL", "").endswith(f"/templates/{filename}")
