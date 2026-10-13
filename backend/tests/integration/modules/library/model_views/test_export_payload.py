"""The metadata export is the escape hatch: what a user can take with them.

`export_payload` backs `GET /api/v1/models/export`, and its promise is in the
payload itself — `contents.kind` is `metadata_only`, and `excludes` names raw
blobs, secrets and printer credentials. Two things go wrong if this file goes
red. Either the export silently drops something a user needs to rebuild their
library elsewhere (a collection path, a tag, a source URL, the provenance chain
that says where a model came from), or it starts carrying something it promised
not to — a storage key, a credential, bytes.

The provenance block is the part worth defending closely. It is assembled from
four tables in three batched queries and then stitched back together in Python,
so an export where every model reports zero sources is a plausible-looking,
completely wrong payload: the queries return rows, the loop groups them under
the wrong key, and nothing raises. Every reference it emits is an *API ref* —
a path a client can call — never a storage key, because a storage key is
internal and a cover's bytes are private to the instance.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from printstash_core.mesh.measurements import (
    VolumeLegacyUnassessed,
    VolumeMeasured,
    VolumeNotCalculated,
    VolumeNotCalculatedCause,
    VolumeUnavailable,
    VolumeUnavailableCause,
)
from sqlmodel import Session, select

import app.modules.library.model_views.exports as models_exports
from app.db.models import (
    SENTINEL_MODEL_HASH,
    ArtifactProvenanceLink,
    CollectionRole,
    FileType,
    Model,
    ModelProvenanceSource,
    ModelSourceCover,
    ModelTagLink,
    ProvenanceCapture,
    Tag,
)
from tests.factories import (
    build_collection,
    build_file,
    build_metadata,
    build_model,
    build_user,
    grant_collection_role,
)

FIRST_CAPTURE_AT = datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc)
SECOND_CAPTURE_AT = FIRST_CAPTURE_AT + timedelta(days=7)
COVER_UPDATED_AT = datetime(2026, 4, 2, 12, 30, tzinfo=timezone.utc)
# The exported spellings carry no `+00:00`: SQLite stores a `datetime` column
# without its offset and hands it back naive, so `.isoformat()` in the export has
# nothing to append. Spelling the expected string out keeps that visible rather
# than hiding it behind `.isoformat()` on the aware value we passed in.
FIRST_CAPTURE_ISO = "2026-03-01T09:00:00"
COVER_UPDATED_ISO = "2026-04-02T12:30:00"


def _source(
    session: Session, model: Model, *, tags: list[str] | None = None
) -> ModelProvenanceSource:
    row = ModelProvenanceSource(
        model_id=model.id,
        provider="printables",
        source_item_id="123456",
        canonical_url="https://www.printables.com/model/123456",
        identity_key="a" * 64,
        source_revision="rev-7",
        tags_json=json.dumps(tags if tags is not None else ["functional"]),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


class TestExportPayload:
    def test_reports_the_models_taxonomy_in_the_export(self, db_session: Session):
        user = build_user(db_session, "export-names", superuser=True)
        collection = build_collection(
            db_session, name="ExportCol", slug="export-col", path="export-col"
        )
        model = build_model(db_session, "Exportable")
        model.collection_id = collection.id
        tag = Tag(name="Neat", slug="neat")
        db_session.add_all([model, tag])
        db_session.commit()
        db_session.refresh(tag)
        db_session.add(ModelTagLink(model_id=model.id, tag_id=tag.id))
        build_file(db_session, model, file_type=FileType.STL, filename="exportable.stl")

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        assert row["collection"] == "export-col"
        assert row["tags"] == ["Neat"]

    def test_counts_everything_the_export_contains(self, db_session: Session):
        user = build_user(db_session, "export-counts", superuser=True)
        model = build_model(db_session, "Counted")
        build_file(db_session, model, file_type=FileType.STL, filename="counted.stl")
        build_file(db_session, model, file_type=FileType.STL, filename="counted-b.stl")

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        assert (len(row["files"]), payload["counts"]["files"]) == (2, 2)

    def test_carries_the_provenance_source_identity(self, db_session: Session):
        user = build_user(db_session, "export-source", superuser=True)
        model = build_model(db_session, "Sourced")
        source = _source(db_session, model)

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        assert row["provenance"]["sources"] == [
            {
                "id": source.id,
                "provider": "printables",
                "source_item_id": "123456",
                "canonical_url": "https://www.printables.com/model/123456",
                "source_revision": "rev-7",
                "tags": ["functional"],
                "captures": [],
                "artifacts": [],
                "cover": None,
            }
        ]

    def test_carries_each_capture_field(self, db_session: Session):
        user = build_user(db_session, "export-capture-fields", superuser=True)
        model = build_model(db_session, "Captured")
        source = _source(db_session, model)
        db_session.add(
            ProvenanceCapture(
                provenance_source_id=source.id,
                adapter_version="printables/1",
                source_revision="rev-1",
                snapshot_json='{"title": "Cube"}',
                snapshot_sha256="1" * 64,
                captured_at=FIRST_CAPTURE_AT,
            )
        )
        db_session.commit()

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        # The snapshot body itself is deliberately absent: the export is a
        # summary, and a snapshot is a whole provider page.
        assert row["provenance"]["sources"][0]["captures"] == [
            {
                "snapshot_sha256": "1" * 64,
                "adapter_version": "printables/1",
                "source_revision": "rev-1",
                "captured_at": FIRST_CAPTURE_ISO,
            }
        ]

    def test_orders_the_captures_newest_first(self, db_session: Session):
        user = build_user(db_session, "export-captures", superuser=True)
        model = build_model(db_session, "Recaptured")
        source = _source(db_session, model)
        db_session.add_all(
            [
                ProvenanceCapture(
                    provenance_source_id=source.id,
                    adapter_version="printables/1",
                    source_revision="rev-1",
                    snapshot_json="{}",
                    snapshot_sha256="1" * 64,
                    captured_at=FIRST_CAPTURE_AT,
                ),
                ProvenanceCapture(
                    provenance_source_id=source.id,
                    adapter_version="printables/2",
                    source_revision="rev-2",
                    snapshot_json="{}",
                    snapshot_sha256="2" * 64,
                    captured_at=SECOND_CAPTURE_AT,
                ),
            ]
        )
        db_session.commit()

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        captures = row["provenance"]["sources"][0]["captures"]
        assert [capture["snapshot_sha256"] for capture in captures] == [
            "2" * 64,
            "1" * 64,
        ]

    def test_links_an_artifact_to_its_source_file_by_api_ref(self, db_session: Session):
        user = build_user(db_session, "export-links", superuser=True)
        model = build_model(db_session, "Linked")
        source = _source(db_session, model)
        artifact = build_file(
            db_session, model, file_type=FileType.STL, filename="linked.stl"
        )
        db_session.add(
            ArtifactProvenanceLink(
                file_id=artifact.id,
                provenance_source_id=source.id,
                source_file_id="file-42",
                source_filename="linked-original.stl",
                source_revision="rev-7",
                blob_sha256="c" * 64,
                import_key="d" * 64,
            )
        )
        db_session.commit()

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        assert row["provenance"]["sources"][0]["artifacts"] == [
            {
                "artifact_id": artifact.id,
                "artifact_api_ref": f"/api/v1/files/{artifact.id}/download",
                "source_file_id": "file-42",
                "source_filename": "linked-original.stl",
                "source_revision": "rev-7",
                "blob_sha256": "c" * 64,
            }
        ]

    def test_describes_a_cover_without_leaking_its_storage_key(
        self, db_session: Session
    ):
        user = build_user(db_session, "export-cover", superuser=True)
        model = build_model(db_session, "Covered")
        source = _source(db_session, model)
        db_session.add(
            ModelSourceCover(
                provenance_source_id=source.id,
                storage_key="covers/secret-internal-key.webp",
                content_type="image/webp",
                size_bytes=2048,
                updated_at=COVER_UPDATED_AT,
            )
        )
        db_session.commit()

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        assert row["provenance"]["sources"][0]["cover"] == {
            "api_ref": f"/api/v1/models/{model.id}/provenance/{source.id}/cover",
            "content_api_ref": (
                f"/api/v1/models/{model.id}/provenance/{source.id}/cover/content"
            ),
            "content_type": "image/webp",
            "size_bytes": 2048,
            "updated_at": COVER_UPDATED_ISO,
        }

    def test_groups_each_source_under_the_model_that_owns_it(self, db_session: Session):
        user = build_user(db_session, "export-grouping", superuser=True)
        first = build_model(db_session, "First")
        second = build_model(db_session, "Second")
        _source(db_session, first, tags=["first-only"])
        _source(db_session, second, tags=["second-only"])

        payload = models_exports.export_payload(db_session, user)

        by_id = {m["id"]: m for m in payload["models"]}
        assert (
            by_id[first.id]["provenance"]["sources"][0]["tags"],
            by_id[second.id]["provenance"]["sources"][0]["tags"],
        ) == (["first-only"], ["second-only"])

    def test_omits_a_trashed_artifact_from_a_models_files(self, db_session: Session):
        user = build_user(db_session, "export-trashed-file", superuser=True)
        model = build_model(db_session, "Partly trashed")
        kept = build_file(
            db_session, model, file_type=FileType.STL, filename="kept.stl"
        )
        discarded = build_file(
            db_session, model, file_type=FileType.STL, filename="discarded.stl"
        )
        discarded.deleted_at = datetime.now(timezone.utc)
        db_session.add(discarded)
        db_session.commit()

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        # An export listing a file the user already trashed would restore it on
        # the next import.
        assert [f["id"] for f in row["files"]] == [kept.id]

    def test_exports_only_models_in_collections_the_user_can_view(
        self, db_session: Session
    ):
        member = build_user(db_session, "export-member")
        visible = build_collection(
            db_session, name="Visible", slug="visible", path="visible"
        )
        hidden = build_collection(
            db_session, name="Hidden", slug="hidden", path="hidden"
        )
        db_session.add_all([member, visible, hidden])
        db_session.commit()
        db_session.refresh(member)
        db_session.refresh(visible)
        db_session.refresh(hidden)
        grant_collection_role(db_session, member, visible, CollectionRole.VIEW)
        allowed = build_model(db_session, "Allowed")
        allowed.collection_id = visible.id
        denied = build_model(db_session, "Denied")
        denied.collection_id = hidden.id
        db_session.add_all([allowed, denied])
        db_session.commit()

        payload = models_exports.export_payload(db_session, member)

        assert [m["id"] for m in payload["models"]] == [allowed.id]

    def test_exports_nothing_for_a_user_granted_no_collection(
        self, db_session: Session
    ):
        member = build_user(db_session, "export-outsider")
        db_session.add(member)
        db_session.commit()
        db_session.refresh(member)
        build_model(db_session, "Unreachable")

        payload = models_exports.export_payload(db_session, member)

        # No grants is not "everything the superuser sees"; it is nothing.
        assert payload["models"] == []

    def test_reports_a_model_with_no_provenance_as_an_empty_source_list(
        self, db_session: Session
    ):
        user = build_user(db_session, "export-no-provenance", superuser=True)
        model = build_model(db_session, "Bare")

        payload = models_exports.export_payload(db_session, user)

        row = next(m for m in payload["models"] if m["id"] == model.id)
        assert row["provenance"] == {"schema_version": 2, "sources": []}

    def test_is_empty_when_no_model_is_accessible(self, db_session: Session):
        user = build_user(db_session, "export-empty", superuser=True)
        # A fresh database still holds the sentinel model, which never exports.
        for row in db_session.exec(
            select(Model).where(Model.hash != SENTINEL_MODEL_HASH)
        ).all():
            row.deleted_at = datetime.now(timezone.utc)
            db_session.add(row)
        db_session.commit()

        payload = models_exports.export_payload(db_session, user)

        assert (payload["models"], payload["counts"]) == ([], {"models": 0, "files": 0})


class TestExportVolumeEvidence:
    @pytest.mark.parametrize(
        ("volume", "expected"),
        [
            (
                VolumeMeasured(1e-9),
                ("measured", "mm3", "mesh_surface_integral", "", "1e-09"),
            ),
            (
                VolumeUnavailable(VolumeUnavailableCause.INCONSISTENT_WINDING),
                (
                    "unavailable",
                    "mm3",
                    "mesh_surface_integral",
                    "inconsistent_winding",
                    "",
                ),
            ),
            (
                VolumeNotCalculated(VolumeNotCalculatedCause.TOPOLOGY_NOT_EVALUATED),
                ("not_calculated", "mm3", "", "topology_not_evaluated", ""),
            ),
            (VolumeLegacyUnassessed(0.0), ("legacy_unassessed", "mm3", "", "", "0.0")),
            (VolumeLegacyUnassessed(None), ("legacy_unassessed", "mm3", "", "", "")),
        ],
    )
    def test_export_preserves_volume_evidence(self, db_session, volume, expected):
        user = build_user(db_session, "volume-export", superuser=True)
        model = build_model(db_session, "Measured mesh")
        artifact = build_file(
            db_session, model, filename="measured.stl", file_type=FileType.STL
        )
        build_metadata(db_session, artifact, volume=volume)
        payload = models_exports.export_payload(db_session, user)
        wire = payload["models"][0]["files"][0]["metadata"]["volume_measurement"]
        assert wire["state"] == expected[0]
        assert wire["unit"] == expected[1]
        assert (
            wire["value_mm3"]
            == payload["models"][0]["files"][0]["metadata"]["volume_mm3"]
        )
        row = next(csv.DictReader(io.StringIO(models_exports.export_csv(payload))))
        assert (
            tuple(
                row[column]
                for column in (
                    "volume_state",
                    "volume_unit",
                    "volume_method",
                    "volume_cause",
                    "volume_mm3",
                )
            )
            == expected
        )
        assert list(row)[-5:] == [
            "triangle_count",
            "volume_state",
            "volume_unit",
            "volume_method",
            "volume_cause",
        ]

    def test_csv_keeps_absent_metadata_distinct_from_volume_evidence(self, db_session):
        user = build_user(db_session, "no-volume-export", superuser=True)
        model = build_model(db_session, "Unenriched mesh")
        build_file(db_session, model, filename="unenriched.stl", file_type=FileType.STL)
        payload = models_exports.export_payload(db_session, user)
        assert payload["models"][0]["files"][0]["metadata"] is None
        row = next(csv.DictReader(io.StringIO(models_exports.export_csv(payload))))
        assert tuple(
            row[column]
            for column in (
                "volume_state",
                "volume_unit",
                "volume_method",
                "volume_cause",
                "volume_mm3",
            )
        ) == ("", "", "", "", "")
