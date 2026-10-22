"""The app-store manifests under ``catalogues/`` describe the same installation.

Each store gets its own file, and nothing in the product imports them, so a manifest
that drifts from the image it runs fails only on a user's server: a store listing
pinned to an old version, a missing ``/data`` mount that starts every restart empty,
an empty ``VAULT_JWT_SECRET`` read as a deliberate choice, or an administrator field
that never reaches ``VAULT_SETUP_ADMIN_*``. These tests make each of those visible in
the pull request that causes it. The version pin is what ties a release to its
listings: the release commit bumps the app version, and this file fails until every
manifest follows.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from PIL import Image

from app.core.config import Settings, settings
from app.modules.administration import setup_policy
from app.schemas.setup import SetupRequest
from tests.paths import REPO_ROOT

IMAGE = "ghcr.io/xiao-villamor/printstash"
CATALOGUES = REPO_ROOT / "catalogues"
RUNTIPI = CATALOGUES / "runtipi" / "printstash"
UMBREL = CATALOGUES / "umbrel" / "printstash"
CASAOS = CATALOGUES / "casaos" / "PrintStash"
COSMOS = CATALOGUES / "cosmos" / "PrintStash"
TRUENAS = CATALOGUES / "truenas" / "printstash"
# TrueNAS takes a package (a Jinja template plus the app metadata around it)
# rather than a compose document, so it has its own class instead of a
# MAIN_SERVICES entry.
TEMPLATED = {"truenas"}

# The service that runs PrintStash in each store's compose file. Cosmos keeps the
# service name a template variable, so its key is the placeholder the store renders.
MAIN_SERVICES = {
    "casaos": (CASAOS / "docker-compose.yml", "printstash"),
    "cosmos": (COSMOS / "cosmos-compose.json", "{ServiceName}"),
    "runtipi": (RUNTIPI / "docker-compose.yml", "printstash"),
    "umbrel": (UMBREL / "docker-compose.yml", "web"),
}
STORES = sorted(MAIN_SERVICES)
# Stores whose install always supplies VAULT_SETUP_ADMIN_* (required form fields on
# Runtipi, Umbrel's per-app credentials, Cosmos' install dialog), and those whose
# fields may be left blank.
ALWAYS_PROVISIONED = ["cosmos", "runtipi", "umbrel"]
OPTIONALLY_PROVISIONED = ["casaos"]
ADMIN_SETTINGS = ["VAULT_SETUP_ADMIN_USERNAME", "VAULT_SETUP_ADMIN_PASSWORD"]
# The limits the browser wizard and VAULT_SETUP_ADMIN_* validation enforce.
WIZARD_LIMITS = SetupRequest.model_json_schema()["properties"]


def _load(path: Path) -> dict[str, Any]:
    """A store's compose document, in whichever format that store reads."""
    if path.suffix == ".json":
        return json.loads(path.read_text())

    return yaml.safe_load(path.read_text())


def _service(store: str) -> dict[str, Any]:
    path, name = MAIN_SERVICES[store]
    return _load(path)["services"][name]


def _environment(store: str) -> dict[str, str]:
    """Environment as a mapping, whichever of Compose's two forms the store uses."""
    raw = _service(store)["environment"]
    if isinstance(raw, dict):
        return {str(key): str(value) for key, value in raw.items()}
    return dict(str(entry).split("=", 1) for entry in raw)


def _runtipi_fields() -> dict[str, dict[str, Any]]:
    config = json.loads((RUNTIPI / "config.json").read_text())
    return {item["env_variable"]: item for item in config["form_fields"]}


def _cosmos_form() -> dict[str, dict[str, Any]]:
    installer = _load(MAIN_SERVICES["cosmos"][0])["cosmos-installer"]
    return {field["name"]: field for field in installer["form"]}


class TestEveryCatalogue:
    def test_covers_every_catalogue_directory(self) -> None:
        # A new store directory is guarded the day it is added, or this fails.
        stores = {path.name for path in CATALOGUES.iterdir() if path.is_dir()}

        assert stores == set(MAIN_SERVICES) | TEMPLATED, (
            "add the new store to MAIN_SERVICES, or to TEMPLATED when it ships a template"
        )

    def test_classifies_every_store_by_how_it_provisions_the_owner(self) -> None:
        # Each store's setup mode follows from this, so none may be left out.
        assert sorted(ALWAYS_PROVISIONED + OPTIONALLY_PROVISIONED) == STORES

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_runs_the_unified_image_at_the_app_version(self, store: str) -> None:
        image = _service(store)["image"]

        # Umbrel appends the multi-arch index digest it pins. The tag in front of
        # it is still what ties the manifest to the release.
        reference = image.split("@", 1)[0]

        assert reference == f"{IMAGE}:{settings.app_version}", (
            "bump every catalogue manifest in the release commit"
        )

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_persists_the_data_root(self, store: str) -> None:
        targets = [
            volume["target"] if isinstance(volume, dict) else volume.split(":")[1]
            for volume in _service(store)["volumes"]
        ]

        assert "/data" in targets, "without /data every restart starts empty"

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_ships_compute_budgets_the_settings_accept(self, store: str) -> None:
        env = _environment(store)

        resolved = Settings(
            compute_mode=env["VAULT_COMPUTE_MODE"],
            compute_memory_mb=int(env["VAULT_COMPUTE_MEMORY_MB"]),
            mesh_memory_budget_fraction=float(env["VAULT_MESH_MEMORY_BUDGET_FRACTION"]),
            embedding_memory_budget_fraction=float(
                env["VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION"]
            ),
        )

        assert resolved.compute_mode == "auto"

    def test_describes_the_casaos_budgets_in_the_install_dialog(self) -> None:
        described = {
            item["container"]
            for item in _load(MAIN_SERVICES["casaos"][0])["services"]["printstash"][
                "x-casaos"
            ]["envs"]
        }

        assert {
            "VAULT_COMPUTE_MEMORY_MB",
            "VAULT_MESH_MEMORY_BUDGET_FRACTION",
            "VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION",
        } <= described

    @pytest.mark.parametrize("store", ALWAYS_PROVISIONED, ids=str)
    def test_creates_the_owner_from_the_environment_where_it_is_always_supplied(
        self, store: str
    ) -> None:
        # The owner exists before the first request and the browser has no door,
        # so nothing through the store's proxy can claim the installation.
        assert _environment(store)["VAULT_SETUP_MODE"] == "environment"

    @pytest.mark.parametrize("store", OPTIONALLY_PROVISIONED, ids=str)
    def test_ships_defaults_that_register_in_the_browser(self, store: str) -> None:
        # Resolved by the real policy: an untouched install dialog must not boot
        # misconfigured.
        environment = _environment(store)

        policy = setup_policy.resolve(
            environment["VAULT_SETUP_MODE"],
            environment["VAULT_SETUP_ADMIN_USERNAME"],
            environment["VAULT_SETUP_ADMIN_PASSWORD"],
            environment["VAULT_SETUP_ADMIN_EMAIL"],
        )

        assert isinstance(policy, setup_policy.TrustedNetwork), policy

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_never_passes_a_jwt_secret(self, store: str) -> None:
        assert "VAULT_JWT_SECRET" not in _environment(store), (
            "an empty value is read as a deliberate choice and skips the generated one"
        )

    @pytest.mark.parametrize("store", STORES, ids=str)
    @pytest.mark.parametrize("variable", ADMIN_SETTINGS, ids=str)
    def test_wires_the_environment_administrator(
        self, store: str, variable: str
    ) -> None:
        assert variable in _environment(store)

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_offers_the_settings_restart(self, store: str) -> None:
        assert _environment(store)["VAULT_RESTART_ENABLED"] == "true"

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_relaunches_after_a_graceful_exit(self, store: str) -> None:
        assert _service(store)["restart"] == "unless-stopped", (
            "a Settings restart is a graceful SIGTERM that can exit 0; "
            "on-failure would leave the app stopped"
        )

    @pytest.mark.parametrize("store", STORES, ids=str)
    def test_gives_both_processes_time_to_stop(self, store: str) -> None:
        assert _service(store)["stop_grace_period"] == "60s"


class TestRuntipi:
    def test_routes_to_the_web_port(self) -> None:
        assert _service("runtipi")["x-runtipi"] == {
            "is_main": True,
            "internal_port": 3000,
        }

    def test_declares_the_app_version(self) -> None:
        config = json.loads((RUNTIPI / "config.json").read_text())

        assert config["version"] == settings.app_version

    def test_requires_a_runtipi_that_reads_dynamic_compose(self) -> None:
        # x-runtipi schema_version 2 is not understood by older Runtipi releases.
        config = json.loads((RUNTIPI / "config.json").read_text())

        assert config["min_tipi_version"] == "4.5.0"

    @pytest.mark.parametrize(
        ("field", "variable"),
        [
            pytest.param(
                "PRINTSTASH_ADMIN_USERNAME", "VAULT_SETUP_ADMIN_USERNAME", id="username"
            ),
            pytest.param(
                "PRINTSTASH_ADMIN_PASSWORD", "VAULT_SETUP_ADMIN_PASSWORD", id="password"
            ),
            pytest.param(
                "PRINTSTASH_ADMIN_EMAIL", "VAULT_SETUP_ADMIN_EMAIL", id="email"
            ),
        ],
    )
    def test_passes_each_form_field_to_its_setting(
        self, field: str, variable: str
    ) -> None:
        assert _environment("runtipi")[variable] == f"${{{field}}}"

    @pytest.mark.parametrize(
        "field", ["PRINTSTASH_ADMIN_USERNAME", "PRINTSTASH_ADMIN_PASSWORD"], ids=str
    )
    def test_requires_the_administrator_credentials(self, field: str) -> None:
        # Required fields mean the owner exists before the app serves a request,
        # so no browser ever has to claim it through Runtipi's proxy.
        assert _runtipi_fields()[field]["required"] is True

    @pytest.mark.parametrize(
        ("field", "setting"),
        [
            pytest.param("PRINTSTASH_ADMIN_USERNAME", "username", id="username"),
            pytest.param("PRINTSTASH_ADMIN_PASSWORD", "password", id="password"),
        ],
    )
    def test_enforces_the_wizard_limits(self, field: str, setting: str) -> None:
        # A form that accepts less than startup validation leaves no administrator.
        form = _runtipi_fields()[field]

        assert (form["min"], form["max"]) == (
            WIZARD_LIMITS[setting]["minLength"],
            WIZARD_LIMITS[setting]["maxLength"],
        )


class TestUmbrel:
    def test_uses_a_versioned_release_source_reference(self) -> None:
        # The source package moves before publication, when no digest exists yet.
        # Submitted packages must add the published index digest; see the release
        # procedure. Never carry the previous release's digest to a new tag.
        image = _service("umbrel")["image"]

        assert re.fullmatch(
            rf"{re.escape(IMAGE)}:{re.escape(settings.app_version)}(?:@sha256:[0-9a-f]{{64}})?",
            image,
        ), "use the app version, with a full SHA-256 digest when published"
        if os.environ.get("PRINTSTASH_STORE_SUBMISSION") == "1":
            assert "@sha256:" in image, "store submission requires the published digest"

    def test_declares_the_data_root_the_store_moves(self) -> None:
        # umbrelOS 2.0 moves this folder when a user relocates app data. Without
        # it a relocated install starts empty, and it cannot be removed later.
        manifest = _load(UMBREL / "umbrel-app.yml")

        assert manifest["storage"] == {"dataRoot": "data"}

    def test_ships_the_directory_the_data_mounts_from(self) -> None:
        # compose mounts ${APP_DATA_DIR}/data; a missing source directory is what
        # makes a first start fail before the app can create it.
        assert (UMBREL / "data").is_dir()

    def test_routes_the_app_proxy_to_the_web_service(self) -> None:
        proxy = _load(UMBREL / "docker-compose.yml")["services"]["app_proxy"]

        assert (proxy["environment"]["APP_HOST"], proxy["environment"]["APP_PORT"]) == (
            "printstash_web_1",
            3000,
        ), "Umbrel names containers <app id>_<service>_1"

    def test_declares_the_app_version(self) -> None:
        manifest = _load(UMBREL / "umbrel-app.yml")

        assert manifest["version"] == settings.app_version

    def test_provisions_the_username_umbrel_shows(self) -> None:
        manifest = _load(UMBREL / "umbrel-app.yml")

        assert (
            _environment("umbrel")["VAULT_SETUP_ADMIN_USERNAME"]
            == manifest["defaultUsername"]
        )

    def test_asks_umbrel_for_a_password_to_show(self) -> None:
        assert _load(UMBREL / "umbrel-app.yml")["deterministicPassword"] is True

    def test_provisions_the_password_umbrel_shows(self) -> None:
        assert _environment("umbrel")["VAULT_SETUP_ADMIN_PASSWORD"] == "${APP_PASSWORD}"


class TestCasaOS:
    def test_routes_the_published_port_to_the_web_port(self) -> None:
        compose = _load(CASAOS / "docker-compose.yml")
        port = _service("casaos")["ports"][0]

        assert (port["target"], port["published"], compose["x-casaos"]["port_map"]) == (
            3000,
            compose["x-casaos"]["port_map"],
            "3378",
        ), "the dashboard opens port_map, so it must be the published port"

    def test_declares_the_app_version(self) -> None:
        compose = _load(CASAOS / "docker-compose.yml")

        assert compose["x-casaos"]["version"] == settings.app_version

    @pytest.mark.parametrize("variable", ADMIN_SETTINGS, ids=str)
    def test_leaves_the_administrator_blank_for_browser_registration(
        self, variable: str
    ) -> None:
        assert _environment("casaos")[variable] == "", (
            "blank is unset, so an untouched install dialog registers in the browser"
        )

    @pytest.mark.parametrize("variable", ["PUID", "PGID"], ids=str)
    def test_uses_the_host_file_identity(self, variable: str) -> None:
        assert _environment("casaos")[variable] == f"${variable}"


class TestCosmos:
    def test_routes_to_the_web_port(self) -> None:
        route = _service("cosmos")["routes"][0]

        assert (route["target"], route["mode"]) == (
            "http://{ServiceName}:3000",
            "SERVAPP",
        )

    def test_installs_the_owner_the_dialog_collects(self) -> None:
        # Cosmos terminates TLS and proxies every app, so registration from the
        # browser through that proxy is refused: the install dialog has to collect
        # the owner, and the manifest has to wire it, or setup stays closed.
        form = _cosmos_form()
        environment = _environment("cosmos")

        assert environment["VAULT_SETUP_ADMIN_USERNAME"] == "{Context.adminUsername}"
        assert environment["VAULT_SETUP_ADMIN_PASSWORD"] == "{Context.adminPassword}"
        assert environment["VAULT_SETUP_ADMIN_EMAIL"] == "{Context.adminEmail}"
        assert form["adminPassword"]["initialValue"] == "{Passwords.0}"

    def test_keeps_the_template_parseable_as_written(self) -> None:
        # The store renders whiskers before parsing. Using no conditionals and no
        # unquoted placeholder keeps this file valid JSON, so the test reads exactly
        # the template a reviewer sees, and the render only substitutes strings.
        manifest = _load(MAIN_SERVICES["cosmos"][0])

        assert isinstance(manifest["services"], dict)
        assert manifest["minVersion"] == "0.8.0"

    def test_names_the_icon_the_store_serves(self) -> None:
        assert _service("cosmos")["labels"]["cosmos-icon"] == (
            "https://azukaar.github.io/cosmos-servapps-official/servapps/"
            "PrintStash/icon.png"
        )

    def test_describes_the_app_the_manifest_installs(self) -> None:
        description = json.loads((COSMOS / "description.json").read_text())

        assert description["image"].removeprefix("https://") == IMAGE, (
            "the store checks description.image against the primary service image"
        )
        assert description["repository"] == (
            "https://github.com/xiao-villamor/PrintStash"
        )
        assert description["supported_architectures"] == ["amd64", "arm64"]

    def test_ships_the_store_assets(self) -> None:
        # The store's Pages build needs a square icon and at least one screenshot;
        # a missing screenshots/ directory crashes the whole deploy.
        icon = Image.open(COSMOS / "icon.png")

        assert icon.size == (256, 256), "the store shows icon.png at 256x256"
        assert len(sorted((COSMOS / "screenshots").glob("*.png"))) >= 1


def _truenas_env() -> dict[str, str]:
    """The variables the TrueNAS template sets itself, read from its source."""
    template = (TRUENAS / "templates" / "docker-compose.yaml").read_text()

    return dict(re.findall(r'add_env\("([A-Z0-9_]+)", "([^"]*)"\)', template))


def _truenas_template() -> str:
    return (TRUENAS / "templates" / "docker-compose.yaml").read_text()


def _truenas_fields() -> dict[str, dict[str, Any]]:
    """The install form's PrintStash group, keyed by variable."""
    questions = _load(TRUENAS / "questions.yaml")["questions"]
    group = next(
        question for question in questions if question["variable"] == "printstash"
    )
    return {attr["variable"]: attr for attr in group["schema"]["attrs"]}


def _truenas_environment_block() -> str:
    """The template lines that run only when the form provisions the owner."""
    # The branch opens and closes at column 0; anything nested inside is indented.
    match = re.search(
        r'^\{% if values\.printstash\.setup_mode == "environment" %\}$(.*?)^\{% endif %\}$',
        _truenas_template(),
        re.DOTALL | re.MULTILINE,
    )
    assert match, (
        "the administrator settings must sit inside the environment-mode branch"
    )
    return match.group(1)


class TestTruenas:
    def test_declares_the_app_version(self) -> None:
        assert _load(TRUENAS / "app.yaml")["app_version"] == settings.app_version

    def test_uses_a_versioned_release_source_reference(self) -> None:
        # Source metadata is prepared before the image is published. A submitted
        # package adds that release's multi-arch index digest after publication.
        tag = _load(TRUENAS / "ix_values.yaml")["images"]["image"]["tag"]

        assert re.fullmatch(
            rf"{re.escape(settings.app_version)}(?:@sha256:[0-9a-f]{{64}})?", tag
        ), "use the app version, with a full SHA-256 digest when published"
        if os.environ.get("PRINTSTASH_STORE_SUBMISSION") == "1":
            assert "@sha256:" in tag, "store submission requires the published digest"

    def test_persists_the_data_root(self) -> None:
        assert 'add_storage("/data"' in _truenas_template(), (
            "without /data every restart starts empty"
        )

    def test_maps_the_published_port_onto_the_fixed_container_port(self) -> None:
        # The image's nginx listens on 3000 and cannot be moved, so the published
        # port is mapped onto it instead of assuming the two match.
        assert (
            'add_port(values.network.web_port, {"container_port": 3000})'
            in _truenas_template()
        )

    def test_registers_in_the_browser_by_default(self) -> None:
        # The administrator fields are hidden until the form asks for them, so an
        # untouched install registers from the local network.
        mode = _truenas_fields()["setup_mode"]["schema"]["default"]

        policy = setup_policy.resolve(mode, "", "", "")

        assert isinstance(policy, setup_policy.TrustedNetwork), policy

    def test_offers_both_setup_modes(self) -> None:
        # The form is the only way past browser registration for an install first
        # reached through a VPN or a reverse proxy, which the browser path refuses.
        enum = _truenas_fields()["setup_mode"]["schema"]["enum"]

        assert {option["value"] for option in enum} == {
            "trusted_network",
            "environment",
        }

    def test_passes_the_setup_mode_from_the_form(self) -> None:
        assert (
            'add_env("VAULT_SETUP_MODE", values.printstash.setup_mode)'
            in _truenas_template()
        )

    @pytest.mark.parametrize(
        ("field", "variable"),
        [
            pytest.param("admin_username", "VAULT_SETUP_ADMIN_USERNAME", id="username"),
            pytest.param("admin_password", "VAULT_SETUP_ADMIN_PASSWORD", id="password"),
            pytest.param("admin_email", "VAULT_SETUP_ADMIN_EMAIL", id="email"),
        ],
    )
    def test_passes_each_form_field_only_in_environment_mode(
        self, field: str, variable: str
    ) -> None:
        # Credentials alongside trusted_network are a misconfiguration that keeps
        # setup closed, so the template passes them only with the environment mode.
        assert (
            f'add_env("{variable}", values.printstash.{field})'
            in _truenas_environment_block()
        )

    @pytest.mark.parametrize("field", ["admin_username", "admin_password"], ids=str)
    def test_requires_the_administrator_credentials_in_environment_mode(
        self, field: str
    ) -> None:
        schema = _truenas_fields()[field]["schema"]

        assert (schema["required"], schema["show_if"]) == (
            True,
            [["setup_mode", "=", "environment"]],
        )

    @pytest.mark.parametrize(
        ("field", "setting"),
        [
            pytest.param("admin_username", "username", id="username"),
            pytest.param("admin_password", "password", id="password"),
        ],
    )
    def test_enforces_the_wizard_limits(self, field: str, setting: str) -> None:
        # A form that accepts less than startup validation leaves no administrator.
        schema = _truenas_fields()[field]["schema"]

        assert (schema["min_length"], schema["max_length"]) == (
            WIZARD_LIMITS[setting]["minLength"],
            WIZARD_LIMITS[setting]["maxLength"],
        )

    def test_hides_the_password(self) -> None:
        assert _truenas_fields()["admin_password"]["schema"]["private"] is True

    def test_runs_as_the_user_the_install_asks_for(self) -> None:
        # The catalog's permissions container owns the dataset first, so the app
        # never has to start as root to repair ownership itself.
        questions = _load(TRUENAS / "questions.yaml")
        run_as = next(
            question
            for question in questions["questions"]
            if question["variable"] == "run_as"
        )
        defaults = {
            attr["variable"]: attr["schema"]["default"]
            for attr in run_as["schema"]["attrs"]
        }

        assert (
            "set_user(values.run_as.user, values.run_as.group)" in _truenas_template()
        )
        assert defaults == {"user": 568, "group": 568}

    def test_leaves_compute_budgets_to_the_image(self) -> None:
        # The catalog reviewers asked for the image defaults; anyone can still set
        # the budgets through Additional Environment Variables.
        budgets = {
            "VAULT_COMPUTE_MODE",
            "VAULT_COMPUTE_MEMORY_MB",
            "VAULT_MESH_MEMORY_BUDGET_FRACTION",
            "VAULT_EMBEDDING_MEMORY_BUDGET_FRACTION",
        }

        assert not budgets & set(_truenas_env())

    def test_never_passes_a_jwt_secret(self) -> None:
        assert "VAULT_JWT_SECRET" not in _truenas_env(), (
            "an empty value is read as a deliberate choice and skips the generated one"
        )

    def test_offers_the_settings_restart(self) -> None:
        assert _truenas_env()["VAULT_RESTART_ENABLED"] == "true"

    def test_relaunches_after_a_graceful_exit(self) -> None:
        # The catalog library restarts unless-stopped; a Settings restart is a
        # graceful SIGTERM, and running jobs get the image's 60s to finish.
        template = _truenas_template()

        assert "on-failure" not in template
        assert "set_grace_period(60)" in template
