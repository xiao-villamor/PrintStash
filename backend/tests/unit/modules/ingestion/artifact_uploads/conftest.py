"""Exercise upload staging on both ordinary and hardlinkless filesystems."""

import errno
import os
from unittest.mock import Mock

import pytest


@pytest.fixture(params=[True, False], ids=["hardlinks", "unraid"])
def hardlink_support(request, monkeypatch):
    if not request.param:
        monkeypatch.setattr(
            os, "link", Mock(side_effect=PermissionError(errno.EPERM, "no links"))
        )
