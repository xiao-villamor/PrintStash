"""Small Orca 2.5 binding fake for plugin contract tests outside the GUI.

Only the embedded host is represented here. Plugin code, settings parsing,
native-context extraction, hashing and HTTP all remain real. The shape follows
OrcaSlicer's v2.5.0 SlicingPipelinePluginCapability.cpp and PythonPluginBridge.cpp.
This cannot verify the C++ loader, its permission dialogs, or wxWebView itself.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from types import ModuleType, SimpleNamespace

from tests.paths import REPO_ROOT


class Step(Enum):
    posSlice = 1
    psGCodePostProcess = 2


@dataclass
class ExecutionResult:
    status: str
    message: str

    @classmethod
    def success(cls, message=""):
        return cls("success", message)

    @classmethod
    def skipped(cls, message=""):
        return cls("skipped", message)


class Capability:
    def __init__(self):
        self.config = "{}"

    def get_config(self):
        return self.config

    def save_config(self, config):
        json.loads(config)
        self.config = config
        return True


def export_context(path, *, output_name="benchy.gcode", config=None):
    values = {} if config is None else config
    return SimpleNamespace(
        step=Step.psGCodePostProcess,
        gcode_path=str(path),
        output_name=output_name,
        orca_version="2.5.0",
        config_value=values.get,
    )


@contextmanager
def plugin_host():
    """Import the shipped modules under an isolated external-host binding."""
    names = ("orca", "printstash_orca_push", "printstash_orca_plugin")
    previous = {name: sys.modules.get(name) for name in names}
    host = ModuleType("orca")
    packages = []
    capabilities = []
    host.base = object
    host.plugin = lambda cls: packages.append(cls) or cls
    host.register_capability = capabilities.append
    host.ExecutionResult = ExecutionResult
    host.slicing = SimpleNamespace(SlicingPipelineCapabilityBase=Capability, Step=Step)
    sys.modules["orca"] = host
    try:
        for name in names[1:]:
            spec = importlib.util.spec_from_file_location(
                name, REPO_ROOT / "scripts" / (name + ".py")
            )
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        for package in packages:
            package().register_capabilities()
        yield SimpleNamespace(
            module=sys.modules["printstash_orca_plugin"],
            upload=sys.modules["printstash_orca_push"],
            capabilities=capabilities,
        )
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
