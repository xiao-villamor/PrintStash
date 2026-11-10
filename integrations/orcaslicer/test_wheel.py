"""The downloadable wheel contains a loadable Orca entry point without dependencies.

Run with the wheel path as the first argument, including on Python 3.12. Only
Orca's external binding is faked; both plugin modules import from the wheel.
"""

import base64
import csv
import hashlib
import importlib
import io
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from zipfile import ZipFile

WHEEL = Path(sys.argv.pop(1)).resolve()


class TestWheel(unittest.TestCase):
    def test_verifies_packaged_files(self):
        with ZipFile(WHEEL) as archive:
            records = [
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/RECORD")
            ]
            self.assertEqual(len(records), 1)
            rows = list(csv.reader(io.StringIO(archive.read(records[0]).decode())))
            self.assertEqual({row[0] for row in rows}, set(archive.namelist()))
            for name, digest, size in rows:
                if name == records[0]:
                    continue
                content = archive.read(name)
                actual = (
                    base64.urlsafe_b64encode(hashlib.sha256(content).digest())
                    .rstrip(b"=")
                    .decode()
                )
                self.assertEqual(digest, "sha256=" + actual, name)
                self.assertEqual(int(size), len(content), name)
            metadata = archive.read(records[0].replace("RECORD", "METADATA")).decode()
            self.assertNotIn("Requires-Dist:", metadata)
            self.assertIn("Name: printstash-orca-plugin", metadata)

    def test_imports_entry_from_wheel(self):
        host = ModuleType("orca")
        packages = []
        capabilities = []
        host.base = object
        host.plugin = lambda cls: packages.append(cls) or cls
        host.register_capability = capabilities.append
        host.slicing = SimpleNamespace(SlicingPipelineCapabilityBase=object)
        sys.modules["orca"] = host
        sys.path.insert(0, str(WHEEL))
        try:
            entry = importlib.import_module("printstash_orca_plugin")
            self.assertEqual(len(packages), 1)
            packages[0]().register_capabilities()
            self.assertEqual(
                [cap().get_name() for cap in capabilities], ["Upload to PrintStash"]
            )
            self.assertTrue(entry.__file__.startswith(str(WHEEL)))
            self.assertTrue(entry.upload.__file__.startswith(str(WHEEL)))
            self.assertFalse(capabilities[0]().get_default_config()["enabled"])
        finally:
            sys.path.remove(str(WHEEL))
            for name in ("orca", "printstash_orca_plugin", "printstash_orca_push"):
                sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
