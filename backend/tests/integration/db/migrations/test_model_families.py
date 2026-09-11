"""Upgraded Family schemas keep the same constraints as fresh installations.

The canonical/member cycle must survive both online and offline migration paths
without dropping its foreign key on PostgreSQL or rebuilding away SQLite data.
"""

from io import StringIO

import pytest
from alembic.config import Config

from alembic import command
from tests.paths import ALEMBIC_INI


class TestModelFamiliesSchema:
    @pytest.mark.parametrize(
        "url",
        ["sqlite://", "postgresql://test:test@localhost/test"],
        ids=["sqlite", "postgresql"],
    )
    def test_emits_canonical_constraint_offline(self, url):
        output = StringIO()
        config = Config(str(ALEMBIC_INI), output_buffer=output)
        config.set_main_option("sqlalchemy.url", url)

        command.upgrade(config, "f3736257acd2:0118bda3e719", sql=True)

        assert (
            "FOREIGN KEY(canonical_member_id) REFERENCES model_family_members (id)"
            in output.getvalue()
        )
