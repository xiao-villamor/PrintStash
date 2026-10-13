"""The public root API stays compatible without loading printer code for geometry."""

import json
import subprocess
import sys

import pytest

import printstash_core
from printstash_core import printers


class TestPrintstashCore:
    def test_geometry_import_avoids_printer_startup(self):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, sys; "
                    "import printstash_core.mesh.similarity.budgets; "
                    "print(json.dumps([name for name in sys.modules "
                    "if name.startswith('printstash_core.printers')]))"
                ),
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        loaded = json.loads(result.stdout)

        assert loaded == []

    @pytest.mark.parametrize("name", printstash_core.__all__, ids=str)
    def test_preserves_public_exports(self, name):
        value = getattr(printstash_core, name)

        assert value is getattr(printers, name)

    def test_reports_unknown_name(self):
        with pytest.raises(AttributeError, match="missing_public_contract"):
            _ = printstash_core.missing_public_contract

    def test_lists_public_names(self):
        names = dir(printstash_core)

        assert set(printstash_core.__all__).issubset(names)
