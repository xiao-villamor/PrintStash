"""A disposable mesh process imports only the configuration it consumes."""

import json
import subprocess
import sys
import textwrap
from pathlib import Path

from app.core.native_settings import capture


class TestNativeSettings:
    def test_worker_import_avoids_application_configuration(self):
        script = """
    import json, sys
    from app.core.native_settings import install
    install(json.loads(sys.argv[1]))
    import app.modules.media.mesh_worker
    print(json.dumps({
        "application_config": "app.core.config" in sys.modules,
        "printers": any(n.startswith("printstash_core.printers") for n in sys.modules),
    }))
    """
        result = subprocess.run(
            [sys.executable, "-c", textwrap.dedent(script), json.dumps(capture())],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )

        assert json.loads(result.stdout.strip().splitlines()[-1]) == {
            "application_config": False,
            "printers": False,
        }
