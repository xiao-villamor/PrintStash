"""Shared local state and live-S3 fixtures for backup contracts."""

from tests.integration.services.backup._backup_shared import backup_env as backup_env
from tests.integration.services.backup._backup_shared import (
    backup_s3_env as backup_s3_env,
)
