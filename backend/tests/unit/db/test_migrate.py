"""Migration configuration preserves URL escapes without opening a connection."""

from app.db.migrate import _alembic_config


class TestMigrationConfig:
    def test_preserves_encoded_database_urls(self):
        url = "postgresql+psycopg://fixture:p%25ss@db.invalid/vault?options=-csearch_path%3Dlibrary"
        assert _alembic_config(url).get_main_option("sqlalchemy.url") == url
