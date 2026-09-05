"""Administrator declarations are durable evidence tied to exact targets."""

import json

import pytest
from sqlmodel import Session

from app.services.storage_identity import (
    identity_evidence,
    independent_evidence,
    s3_target,
)
from tests.factories import build_failure_domain_declaration


class TestFailureDomains:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("target_identity", "invalid-json"),
            ("target_identity", "{}"),
            ("failure_domain", "INVALID VALUE"),
            ("revision", "invalid"),
        ],
    )
    def test_invalid_declarations_remain_ineligible(
        self, db_session: Session, field: str, value: str
    ) -> None:
        target = s3_target(endpoint="https://replica.example.test", bucket="backup")
        declaration = build_failure_domain_declaration(db_session, target)
        setattr(declaration, field, value)
        db_session.add(declaration)
        db_session.commit()

        assert identity_evidence(target) is None

    def test_unknown_custom_targets_remain_ineligible(
        self, db_session: Session
    ) -> None:
        active = s3_target(endpoint="https://active.example.test", bucket="vault")
        backup = s3_target(endpoint="https://replica.example.test", bucket="backup")
        build_failure_domain_declaration(db_session, backup)

        assert independent_evidence(active, backup) is None
        assert identity_evidence(None) is None

    def test_distinct_declared_domains_supply_current_evidence(
        self, db_session: Session
    ) -> None:
        active = s3_target(endpoint="https://active.example.test", bucket="vault")
        backup = s3_target(endpoint="https://replica.example.test", bucket="backup")
        build_failure_domain_declaration(db_session, active, failure_domain="home-nas")
        declaration = build_failure_domain_declaration(
            db_session, backup, failure_domain="off-site"
        )

        evidence = independent_evidence(active, backup)

        assert evidence is not None
        assert evidence[0]["failure_domain"] == "administrator:home-nas"
        assert evidence[1]["declaration_revision"] == declaration.revision
        assert evidence[1]["target"] == backup.model_dump()
        assert json.loads(declaration.target_identity) == backup.model_dump()

    def test_shared_declarations_do_not_establish_independence(
        self, db_session: Session
    ) -> None:
        active = s3_target(endpoint="https://active.example.test", bucket="vault")
        backup = s3_target(endpoint="https://replica.example.test", bucket="backup")
        build_failure_domain_declaration(db_session, active, failure_domain="same-nas")
        build_failure_domain_declaration(db_session, backup, failure_domain="same-nas")

        assert independent_evidence(active, backup) is None

    def test_declarations_cannot_override_shared_endpoint_evidence(
        self, db_session: Session
    ) -> None:
        active = s3_target(endpoint="https://same.example.test:9000", bucket="vault")
        backup = s3_target(endpoint="https://same.example.test:9001", bucket="backup")
        build_failure_domain_declaration(db_session, active, failure_domain="one")
        build_failure_domain_declaration(db_session, backup, failure_domain="two")

        assert independent_evidence(active, backup) is None

    def test_target_edits_do_not_inherit_a_declaration(
        self, db_session: Session
    ) -> None:
        target = s3_target(endpoint="https://replica.example.test", bucket="backup")
        build_failure_domain_declaration(db_session, target)
        edited = s3_target(endpoint="https://edited.example.test", bucket="backup")

        assert identity_evidence(edited) is None

    def test_withdrawal_removes_current_trust(self, db_session: Session) -> None:
        target = s3_target(endpoint="https://replica.example.test", bucket="backup")
        declaration = build_failure_domain_declaration(db_session, target)
        assert identity_evidence(target) is not None
        db_session.delete(declaration)
        db_session.commit()

        assert identity_evidence(target) is None
