"""The services the tests run against are the services the app ships against.

`tests/containers.py` starts a PostgreSQL and a SeaweedFS for the `postgres`- and
`s3`-marked tests. The compose stacks start the same two for a real installation.
Nothing connects those two facts, so they can drift silently — and the drift is
invisible in the worst direction: the tests keep passing against a version nobody
runs, while the version people do run is exercised by nothing.

The S3 pin is the one that matters most. SeaweedFS's S3 gateway has changed its
conditional-write and version-id behaviour between releases, and those are exactly
what `contract/services/test_storage_backend.py` asserts. A test suite pinned to a
different digest than the shipped image is a suite that proves the wrong thing.

So both pins live in one place each and are compared here. When you bump the image
in compose, this fails until the tests follow — which is the reminder to re-run
them against the new version before shipping it.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from tests import containers

REPO_ROOT = Path(__file__).resolve().parents[3]

# Every compose file that runs one of these services for real. A new stack that
# pins its own version has to be added here, or it is a third definition nobody
# is comparing.
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.manual-test.yml")


def _service_images(service: str) -> dict[str, str]:
    """The image each compose file pins for *service*, keyed by file name."""
    found: dict[str, str] = {}
    for name in COMPOSE_FILES:
        config = yaml.safe_load((REPO_ROOT / name).read_text(encoding="utf-8"))
        definition = config.get("services", {}).get(service)
        if definition and "image" in definition:
            found[name] = definition["image"]
    return found


class TestSeaweedfsImage:
    def test_every_compose_stack_pins_the_image_the_tests_exercise(self) -> None:
        pinned = _service_images("seaweedfs")

        assert pinned, "no compose file defines a seaweedfs service any more"
        assert set(pinned.values()) == {containers.SEAWEEDFS_IMAGE}, (
            "the S3 tests run against a different SeaweedFS than the app ships "
            f"with: tests pin {containers.SEAWEEDFS_IMAGE}, compose pins {pinned}. "
            "The gateway's conditional-write and version-id behaviour changes "
            "between releases, which is exactly what those tests assert."
        )

    def test_pins_the_image_by_digest(self) -> None:
        # A floating tag means the tests and the deployment can diverge without
        # either file changing, which is the drift this file exists to prevent.
        assert "@sha256:" in containers.SEAWEEDFS_IMAGE


class TestPostgresImage:
    def test_every_compose_stack_pins_the_major_the_tests_exercise(self) -> None:
        pinned = _service_images("postgres")

        assert pinned, "no compose file defines a postgres service any more"
        major = re.match(r"postgres:(\d+)", containers.POSTGRES_IMAGE)
        assert major, containers.POSTGRES_IMAGE
        for name, image in pinned.items():
            assert image.startswith(f"postgres:{major.group(1)}"), (
                f"{name} runs PostgreSQL {image} while the dialect contracts are "
                f"exercised against {containers.POSTGRES_IMAGE}"
            )
