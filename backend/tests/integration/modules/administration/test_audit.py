"""The audit log must not become the place the secrets ended up.

Audit rows record what changed, which for provenance means a diff of captured
metadata — and that metadata contains remote identifiers and whole provider
snapshots. An audit trail is long-lived, widely readable, and exported, so it is
the worst place for either to land.

Both paths are covered because they are separate code: the explicit diff builder,
and the ORM listener that fires on insert and update. Redacting one and not the
other leaves the trail intact through the other.
"""

from __future__ import annotations

import json

from sqlmodel import select

from app.db.models import (
    ArtifactProvenanceLink,
    AuditLog,
    FileType,
    ModelProvenanceSource,
    ProvenanceCapture,
)
from app.modules.administration.audit import _diff_for_obj, install_audit_listeners
from tests.factories import build_file, build_model


class TestAuditListener:
    def test_a_provenance_audit_diff_redacts_every_identifier(
        self,
        db_session,
    ) -> None:
        model = build_model(db_session, name="Bracket", slug="bracket", hash="a" * 64)
        row = ModelProvenanceSource(
            model_id=model.id,
            provider="printables",
            source_item_id="private-remote-id",
            canonical_url="https://example.test/private?token=secret",
            identity_key="b" * 64,
        )
        db_session.add(row)
        db_session.flush()
        row.provider = "printables-v2"
        row.source_item_id = "private-remote-id-v2"
        row.canonical_url = "https://example.test/private?token=other-secret"

        diff = _diff_for_obj(row)
        encoded = str(diff)
        assert "private-remote-id" not in encoded
        assert "private-remote-id-v2" not in encoded
        assert "token=secret" not in encoded
        assert "other-secret" not in encoded
        assert diff["provider"]["after"] == "printables-v2"
        assert diff["source_item_id"]["after"] == "[redacted]"

    def test_the_audit_listener_redacts_provenance_on_every_write(
        self, db_session
    ) -> None:
        install_audit_listeners()
        model = build_model(
            db_session, name="Audit bracket", slug="audit-bracket", hash="c" * 64
        )
        artifact = build_file(
            db_session,
            model,
            path="provenance/audit.stl",
            filename="audit.stl",
            file_type=FileType.STL,
            size_bytes=1,
            sha256="d" * 64,
        )
        source = ModelProvenanceSource(
            model_id=model.id,
            provider="printables",
            source_item_id="private-source-id",
            canonical_url="https://example.test/private?token=never-log",
            identity_key="e" * 64,
            source_revision="private-revision",
        )
        db_session.add_all([artifact, source])
        db_session.flush()
        capture = ProvenanceCapture(
            provenance_source_id=source.id,
            adapter_version="adapter-v2",
            snapshot_json='{"private":"snapshot-value"}',
            snapshot_sha256="f" * 64,
        )
        db_session.add(capture)
        db_session.flush()
        link = ArtifactProvenanceLink(
            file_id=artifact.id,
            provenance_source_id=source.id,
            capture_id=capture.id,
            source_file_id="private-file-id",
            source_filename="customer-private.stl",
            container_entry_path="private/archive/part.stl",
            source_revision="private-file-revision",
            blob_sha256="a" * 64,
            import_key="b" * 64,
        )
        db_session.add(link)
        db_session.commit()

        source.canonical_url = "https://example.test/rotated?token=still-never-log"
        source.source_revision = "rotated-private-revision"
        db_session.commit()

        rows = db_session.exec(
            select(AuditLog).where(
                AuditLog.resource_type.in_(
                    [
                        "model_provenance_sources",
                        "provenance_captures",
                        "artifact_provenance_links",
                    ]
                )
            )
        ).all()
        assert rows
        raw = "\n".join(row.diff_json for row in rows)
        for secret in (
            "private-source-id",
            "private-revision",
            "snapshot-value",
            "private-file-id",
            "customer-private.stl",
            "private/archive/part.stl",
            "never-log",
            "still-never-log",
        ):
            assert secret not in raw
        assert any(
            json.loads(row.diff_json).get("snapshot_json", {}).get("after")
            == "[redacted]"
            for row in rows
        )


class TestBookkeepingRows:
    """Machine bookkeeping is not an audit event; what a person changed is."""

    def _audited(self, db_session, table: str) -> list[AuditLog]:
        return list(
            db_session.exec(
                select(AuditLog).where(AuditLog.resource_type == table)
            ).all()
        )

    def test_a_job_write_leaves_no_row_trail(self, db_session, make_job) -> None:
        install_audit_listeners()
        job = make_job()
        job.attempts = 1
        db_session.add(job)
        db_session.commit()

        assert self._audited(db_session, "jobs") == []

    def test_a_reconciler_cursor_write_leaves_no_row_trail(self, db_session) -> None:
        from app.db.models import JobKind, ReconcileCursor

        install_audit_listeners()
        db_session.add(ReconcileCursor(source=JobKind.SOURCES_SCAN))
        db_session.commit()

        assert self._audited(db_session, "reconcile_cursors") == []

    def test_a_capacity_claim_leaves_no_row_trail(
        self, db_session, make_capacity_reservation
    ) -> None:
        install_audit_listeners()
        claim = make_capacity_reservation()
        db_session.delete(claim)
        db_session.commit()

        assert self._audited(db_session, "capacity_reservations") == []

    def test_a_library_write_is_still_recorded(self, db_session) -> None:
        install_audit_listeners()
        model = build_model(db_session, name="Kept", slug="kept", hash="9" * 64)
        model.name = "Renamed"
        db_session.add(model)
        db_session.commit()

        actions = {row.action for row in self._audited(db_session, "models")}
        assert "update" in actions
