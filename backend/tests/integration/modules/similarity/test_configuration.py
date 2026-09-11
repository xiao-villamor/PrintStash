"""Invalid stored derivative settings fail closed without affecting primary ingestion."""

import pytest

from app.core.errors import OperationError
from app.modules.similarity.configuration import SimilaritySettings, read_settings


class TestStoredConfiguration:
    def test_admits_dense_geometry_configuration(self, db_session):
        result = read_settings(db_session)

        assert result.triangle_cap == 2_000_000

    def test_preserves_operator_configuration(self, db_session, make_system_config):
        make_system_config(similarity_settings_json='{"triangle_cap":200000}')

        result = read_settings(db_session)

        assert result.triangle_cap == 200_000

    @pytest.mark.parametrize(
        "payload", ["null", "[]", "invalid-json", '{"sample_points": 999999}']
    )
    def test_reports_corrupt_settings(self, db_session, make_system_config, payload):
        make_system_config(similarity_settings_json=payload)
        with pytest.raises(OperationError, match="similarity_configuration_invalid"):
            read_settings(db_session)

    def test_class_override_preserves_global_fallback(self):
        config = SimilaritySettings(
            minimum_confidence=0.94, class_overrides={"remeshed": 0.98}
        )
        assert config.threshold_for("remeshed") == 0.98
        assert config.threshold_for("repaired") == 0.94
