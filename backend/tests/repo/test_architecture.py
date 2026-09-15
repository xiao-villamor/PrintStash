"""Module boundaries cannot regress through private or deferred imports."""

import json
import subprocess
import sys

import pytest

from scripts.architecture import (
    cyclic_edges,
    graph_for,
    implicit_api_exports,
    imports,
    inspect,
    violations,
)
from tests.paths import BACKEND_DIR


class TestImports:
    @pytest.mark.parametrize(
        "source",
        [
            "from app.modules.library import operations\noperations.settings.enabled",
            "import app.modules.library.operations as ops\nops.settings.enabled",
            "def run():\n    from app.modules.library import operations\n    return operations.settings.enabled",
            "from app.modules.library.operations import settings",
        ],
        ids=["module", "alias", "deferred", "direct"],
    )
    def test_rejects_an_operations_unexported_dependency(self, source):
        dependencies = imports(source, "app.api.v1.library")
        sources = {
            "app.modules.library.operations": "from app.core.config import settings"
        }

        result = implicit_api_exports(dependencies, sources)

        assert result == {
            "app.api.v1.library -> app.modules.library.operations.settings"
        }

    def test_allows_an_explicit_public_error_contract(self):
        dependencies = imports(
            "from app.modules.library.operations import OperationError",
            "app.api.v1.library",
        )
        sources = {
            "app.modules.library.operations": (
                "from app.core.errors import OperationError\n"
                "__all__ = ['OperationError']"
            )
        }

        result = implicit_api_exports(dependencies, sources)

        assert result == set()

    def test_allows_an_operation_defined_by_its_owner(self):
        dependencies = imports(
            "from app.modules.library import operations\noperations.update()",
            "app.api.v1.library",
        )
        sources = {"app.modules.library.operations": "def update():\n    pass"}

        result = implicit_api_exports(dependencies, sources)

        assert result == set()

    def test_detects_a_deferred_cycle(self):
        dependencies = imports(
            "def run():\n    from app.modules.b import command", "app.modules.a"
        )
        dependencies |= imports("from app.modules.a import run", "app.modules.b")

        cycles = cyclic_edges(
            graph_for(dependencies, {"app.modules.a", "app.modules.b"})
        )

        assert cycles == {
            "app.modules.a -> app.modules.b",
            "app.modules.b -> app.modules.a",
        }

    def test_excludes_type_only_dependencies_from_cycles(self):
        dependencies = imports(
            "if TYPE_CHECKING:\n    from app.modules.b import command", "app.modules.a"
        )
        dependencies |= imports("from app.modules.a import run", "app.modules.b")

        cycles = cyclic_edges(
            graph_for(dependencies, {"app.modules.a", "app.modules.b"})
        )

        assert cycles == set()

    def test_rejects_a_private_cross_module_import(self):
        dependencies = imports(
            "from app.modules.storage.backend import _client",
            "app.modules.library.commands",
        )

        assert violations(dependencies) == {
            "app.modules.library.commands -> app.modules.storage.backend._client"
        }

    def test_allows_private_implementation_inside_its_owner(self):
        dependencies = imports(
            "from app.modules.storage.backend import _client",
            "app.modules.storage.ownership",
        )

        assert violations(dependencies) == set()

    def test_rejects_private_access_through_a_module_alias(self):
        dependencies = imports(
            "from app.modules.storage import backend as store\nstore._client()",
            "app.modules.library.commands",
        )

        assert violations(dependencies) == {
            "app.modules.library.commands -> app.modules.storage.backend._client"
        }

    def test_rejects_private_access_through_a_qualified_import(self):
        dependencies = imports(
            "import app.modules.storage.backend\napp.modules.storage.backend._client()",
            "app.modules.library.commands",
        )

        assert violations(dependencies) == {
            "app.modules.library.commands -> app.modules.storage.backend._client"
        }

    def test_aliases_do_not_leak_between_source_files(self):
        imports("from app.modules.storage import backend as data", "app.api.first")

        dependencies = imports("data._client()", "app.api.second")

        assert violations(dependencies) == set()

    def test_rejects_imports_from_another_owners_private_file(self):
        dependencies = imports(
            "from app.modules.storage._client import connect",
            "app.modules.library.commands",
        )

        assert violations(dependencies) == {
            "app.modules.library.commands -> app.modules.storage._client.connect"
        }

    def test_resolves_relative_imports(self):
        dependencies = imports("from . import writes", "app.modules.library.reads")
        dependencies |= imports(
            "from .reads import query", "app.modules.library.writes"
        )

        graph = graph_for(
            dependencies, {"app.modules.library.reads", "app.modules.library.writes"}
        )

        assert graph["app.modules.library.reads"] == {"app.modules.library.writes"}


class TestArchitecture:
    def test_keeps_native_sessions_in_the_inference_owner(self):
        consumers = set()
        for path in (BACKEND_DIR / "app").rglob("*.py"):
            name = ".".join(path.relative_to(BACKEND_DIR).with_suffix("").parts)
            for dependency in imports(path.read_text(), name):
                if dependency.target.split(".")[0] == "onnxruntime":
                    consumers.add(dependency.source)
        assert consumers
        assert all(name.startswith("app.modules.inference.") for name in consumers)

    @pytest.mark.parametrize(
        "dependency",
        ["fastapi", "fastapi.responses", "starlette.exceptions", "app.api.errors"],
        ids=["framework", "response", "http-error", "inbound-adapter"],
    )
    def test_rejects_transport_imports_in_product_operations(
        self, tmp_path, dependency
    ):
        module = tmp_path / "app" / "modules" / "library" / "commands.py"
        module.parent.mkdir(parents=True)
        module.write_text(f"def run():\n    import {dependency}\n")

        result = inspect(tmp_path)

        assert result["transport_dependencies"] == [
            f"app.modules.library.commands -> {dependency}"
        ]

    def test_storage_contracts_do_not_initialize_adapters(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; "
                "import app.modules.storage.storage_backend.contracts; "
                "assert not any(name in sys.modules for name in ("
                "'app.modules.storage.storage_backend.runtime',"
                "'app.modules.storage.storage_backend.local',"
                "'app.modules.storage.storage_backend.s3'))",
            ],
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stderr

    def test_application_respects_its_recorded_boundaries(self):
        baseline = json.loads((BACKEND_DIR / "architecture-debt.json").read_text())

        assert not any(baseline.values()), (
            "Resolved debt must not acquire new exceptions."
        )

        current = inspect(BACKEND_DIR)

        assert current == baseline, (
            "Remove resolved debt; new private dependencies or cycles are prohibited."
        )

    def test_no_application_import_uses_the_removed_service_directory(self):
        current = inspect(BACKEND_DIR)

        assert current["legacy_imports"] == []
