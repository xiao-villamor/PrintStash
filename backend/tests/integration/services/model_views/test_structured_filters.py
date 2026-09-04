"""Filtering the library, where the answer must come from one artifact.

The library filters look independent and are not. A model with a PLA mesh and a
PETG G-code file matches "material = PLA" and "material = PETG" separately, but it
must **not** match a query for both — the two facts live on different artifacts,
and a filter that ANDs across them claims a combination that does not exist.

That is the property most of this file defends, and it is invisible in a single
filter: it only appears when two are combined, which is exactly how a user
searches.

The rest are the boundaries each filter has: a date window that includes its
edges, `printed=false` meaning "never printed" rather than "no rows", a tag
matched by slug rather than display name (two tags can render alike), and a
storage filter that distinguishes external from vault-owned rather than treating
absence as either.

These run against real rows because the filters are SQL. A mocked query would
assert the shape of the SQL rather than what it returns, which is the one thing
that matters here.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session

from app.db.models import (
    File,
    FileRevisionStatus,
    FileType,
    PrinterFile,
    PrintJobState,
    Tag,
)
from app.schemas.models import ModelFilters
from app.services import model_views
from tests.factories import (
    build_collection,
    build_file,
    build_model,
    build_print_job,
    build_printer,
    build_user,
)


class TestListItems:
    def test_structured_filters_require_metadata_on_same_artifact(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "filter-admin", superuser=True)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        split = build_model(db_session, "Split")
        build_file(
            db_session,
            split,
            filename="split.stl",
            file_type=FileType.STL,
            metadata={"material_type": "PLA"},
        )
        build_file(
            db_session,
            split,
            filename="split.gcode",
            file_type=FileType.GCODE,
            status=FileRevisionStatus.KNOWN_GOOD,
            metadata={"material_type": "PETG"},
        )
        same = build_model(db_session, "Same")
        build_file(
            db_session,
            same,
            filename="same.gcode",
            file_type=FileType.GCODE,
            status=FileRevisionStatus.KNOWN_GOOD,
            metadata={"material_type": "PLA"},
        )

        rows = model_views.list_items(
            db_session,
            user,
            filters=ModelFilters(
                file_type=[FileType.GCODE],
                material_type=["PLA"],
                revision_status=[FileRevisionStatus.KNOWN_GOOD],
            ),
        )
        assert [row.id for row in rows] == [same.id]

    def test_storage_filter_single_value_matches_external_only(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "storage-admin", superuser=True)
        external = build_model(db_session, "External")
        build_file(db_session, external, filename="e.stl", file_type=FileType.STL)
        vault = build_model(db_session, "Vault")
        build_file(db_session, vault, filename="v.stl", file_type=FileType.STL)
        ext_file = db_session.exec(
            __import__("sqlmodel").select(File).where(File.model_id == external.id)
        ).first()
        ext_file.is_external = True
        db_session.add(ext_file)
        db_session.commit()

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(storage=["external"])
        )
        assert [row.id for row in rows] == [external.id]

    def test_the_uploaded_filters_bound_the_window_at_both_ends(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "uploaded-admin", superuser=True)
        early = build_model(db_session, "Early")
        late = build_model(db_session, "Late")
        early_file = build_file(
            db_session, early, filename="early.stl", file_type=FileType.STL
        )
        late_file = build_file(
            db_session, late, filename="late.stl", file_type=FileType.STL
        )
        early_file.uploaded_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        late_file.uploaded_at = datetime(2030, 1, 1, tzinfo=timezone.utc)
        db_session.add(early_file)
        db_session.add(late_file)
        db_session.commit()

        after = model_views.list_items(
            db_session,
            user,
            filters=ModelFilters(
                uploaded_after=datetime(2025, 1, 1, tzinfo=timezone.utc)
            ),
        )
        assert [row.id for row in after] == [late.id]

        before = model_views.list_items(
            db_session,
            user,
            filters=ModelFilters(
                uploaded_before=datetime(2025, 1, 1, tzinfo=timezone.utc)
            ),
        )
        assert [row.id for row in before] == [early.id]

    def test_the_printed_filter_selects_each_side(self, db_session: Session) -> None:
        user = build_user(db_session, "printed-admin", superuser=True)
        printed = build_model(db_session, "Printed")
        unprinted = build_model(db_session, "Unprinted")
        printed_file = build_file(
            db_session, printed, filename="p.gcode", file_type=FileType.GCODE
        )
        build_print_job(
            db_session,
            printed_file,
            remote_filename="p.gcode",
            state=PrintJobState.COMPLETED,
        )
        db_session.commit()

        was_printed = model_views.list_items(
            db_session, user, filters=ModelFilters(printed=True)
        )
        assert [row.id for row in was_printed] == [printed.id]

        never_printed = model_views.list_items(
            db_session, user, filters=ModelFilters(printed=False)
        )
        assert unprinted.id in [row.id for row in never_printed]
        assert printed.id not in [row.id for row in never_printed]

    def test_print_outcome_filter(self, db_session: Session) -> None:
        user = build_user(db_session, "outcome-admin", superuser=True)
        failed = build_model(db_session, "FailedModel")
        completed = build_model(db_session, "CompletedModel")
        failed_file = build_file(
            db_session, failed, filename="f.gcode", file_type=FileType.GCODE
        )
        completed_file = build_file(
            db_session, completed, filename="c.gcode", file_type=FileType.GCODE
        )
        build_print_job(
            db_session,
            failed_file,
            remote_filename="f.gcode",
            state=PrintJobState.FAILED,
        )
        build_print_job(
            db_session,
            completed_file,
            remote_filename="c.gcode",
            state=PrintJobState.COMPLETED,
        )
        db_session.commit()

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(print_outcome=[PrintJobState.FAILED])
        )
        assert [row.id for row in rows] == [failed.id]

    def test_tag_filter_matches_by_slug(self, db_session: Session) -> None:
        user = build_user(db_session, "tag-admin", superuser=True)
        tagged = build_model(db_session, "Tagged")
        untagged = build_model(db_session, "Untagged")
        tag = Tag(name="Functional", slug="functional")
        db_session.add(tag)
        db_session.commit()
        db_session.refresh(tag)
        from app.db.models import ModelTagLink

        db_session.add(ModelTagLink(model_id=tagged.id, tag_id=tag.id))
        db_session.commit()

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(tag=["functional"])
        )
        ids = {row.id for row in rows}
        assert tagged.id in ids
        assert untagged.id not in ids

    def test_printer_presence_any_matches_models_present_on_a_printer(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "presence-admin", superuser=True)
        present = build_model(db_session, "Present")
        absent = build_model(db_session, "Absent")
        present_file = build_file(
            db_session, present, filename="present.gcode", file_type=FileType.GCODE
        )
        printer = build_printer(
            db_session, name="Fleet1", moonraker_url="http://10.0.0.1:7125"
        )
        db_session.add(
            PrinterFile(
                printer_id=printer.id,
                file_id=present_file.id,
                remote_filename="present.gcode",
            )
        )
        db_session.commit()

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(printer_presence="any")
        )
        ids = {row.id for row in rows}
        assert present.id in ids
        assert absent.id not in ids

    def test_direct_filter_with_collection_restricts_to_exact_path(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "direct-admin", superuser=True)
        parent = build_collection(
            db_session, name="Parent", slug="parent", path="parent"
        )
        child = build_collection(
            db_session, name="Child", slug="child", path="parent/child"
        )
        db_session.add(parent)
        db_session.commit()
        db_session.refresh(parent)
        db_session.refresh(child)
        direct_model = build_model(
            db_session,
            name="Direct",
            slug="direct",
            hash="b" * 64,
            collection_id=parent.id,
        )
        build_model(
            db_session,
            name="Nested",
            slug="nested",
            hash="c" * 64,
            collection_id=child.id,
        )
        db_session.add(direct_model)
        db_session.commit()
        db_session.refresh(direct_model)

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(collection="parent", direct=True)
        )
        assert [row.id for row in rows] == [direct_model.id]

    def test_direct_filter_without_collection_matches_uncategorised_only(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "direct-admin2", superuser=True)
        col = build_collection(db_session, name="Cat", slug="cat", path="cat")
        categorised = build_model(
            db_session,
            name="Categorised",
            slug="categorised",
            hash="d" * 64,
            collection_id=col.id,
        )
        uncategorised = build_model(
            db_session, name="Uncategorised", slug="uncategorised", hash="e" * 64
        )
        db_session.add(categorised)
        db_session.commit()
        db_session.refresh(uncategorised)

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(direct=True)
        )
        ids = {row.id for row in rows}
        assert uncategorised.id in ids
        assert categorised.id not in ids

    def test_indirect_collection_filter_includes_descendants(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "indirect-admin", superuser=True)
        parent = build_collection(
            db_session, name="Parent2", slug="parent2", path="parent2"
        )
        child = build_collection(
            db_session, name="Child2", slug="child2", path="parent2/child2"
        )
        db_session.add(parent)
        db_session.commit()
        db_session.refresh(parent)
        db_session.refresh(child)
        direct_model = build_model(
            db_session,
            name="Direct2",
            slug="direct2",
            hash="f" * 64,
            collection_id=parent.id,
        )
        nested_model = build_model(
            db_session,
            name="Nested2",
            slug="nested2",
            hash="1" * 64,
            collection_id=child.id,
        )
        db_session.add(direct_model)
        db_session.commit()

        rows = model_views.list_items(
            db_session, user, filters=ModelFilters(collection="parent2")
        )
        ids = {row.id for row in rows}
        assert direct_model.id in ids
        assert nested_model.id in ids

    def test_the_slicer_metadata_filters_narrow_by_exact_value(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "slicer-admin", superuser=True)
        orca = build_model(db_session, "Orca")
        prusa = build_model(db_session, "Prusa")
        build_file(
            db_session,
            orca,
            filename="orca.gcode",
            file_type=FileType.GCODE,
            metadata={"slicer_name": "OrcaSlicer", "printer_model": "Voron 2.4"},
        )
        build_file(
            db_session,
            prusa,
            filename="prusa.gcode",
            file_type=FileType.GCODE,
            metadata={"slicer_name": "PrusaSlicer", "printer_model": "MK4"},
        )

        by_slicer = model_views.list_items(
            db_session, user, filters=ModelFilters(slicer_name=["orcaslicer"])
        )
        assert [row.id for row in by_slicer] == [orca.id]

        by_printer = model_views.list_items(
            db_session, user, filters=ModelFilters(printer_model=["mk4"])
        )
        assert [row.id for row in by_printer] == [prusa.id]

    def test_list_items_picks_newest_version_mesh_file_for_preview(
        self,
        db_session: Session,
    ) -> None:
        user = build_user(db_session, "mesh-admin", superuser=True)
        model = build_model(db_session, "MeshPreview")
        v1 = build_file(
            db_session,
            model,
            path="v1.stl",
            filename="v1.stl",
            file_type=FileType.STL,
            size_bytes=1,
            sha256="1" * 64,
        )
        v2 = build_file(
            db_session,
            model,
            path="v2.stl",
            filename="v2.stl",
            file_type=FileType.STL,
            version=2,
            size_bytes=1,
            sha256="2" * 64,
        )
        db_session.add(v1)
        db_session.commit()
        db_session.refresh(v2)

        rows = model_views.list_items(db_session, user, limit=100)
        row = next(r for r in rows if r.id == model.id)
        assert row.mesh_file_id == v2.id


# --------------------------------------------------------------------------- #
# _apply_structured_filters — storage / uploaded window / slicer / printer_model
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _filtered_stmt — direct/indirect collection scoping, tags, printer presence
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# list_items — mesh preview file id (first live mesh file wins)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# print_statistics — invalid period falls back to "30d"
# --------------------------------------------------------------------------- #


class TestFacets:
    def test_facets_count_distinct_models(self, db_session: Session) -> None:
        user = build_user(db_session, "facet-admin", superuser=True)
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        model = build_model(db_session, "Facet")
        build_file(
            db_session,
            model,
            filename="one.stl",
            file_type=FileType.STL,
            metadata={"material_type": "PLA"},
        )
        build_file(
            db_session,
            model,
            filename="two.stl",
            file_type=FileType.STL,
            status=FileRevisionStatus.KNOWN_GOOD,
            metadata={"material_type": "PLA"},
        )
        result = model_views.facets(db_session, user, ModelFilters())
        assert (
            next(item.count for item in result.material_type if item.value == "PLA")
            == 1
        )
        assert [item.model_dump() for item in result.file_type] == [
            {"value": "stl", "count": 1}
        ]
        assert [item.model_dump() for item in result.revision_status] == [
            {"value": "known_good", "count": 1}
        ]
        assert [item.model_dump() for item in result.storage] == [
            {"value": "vault", "count": 1}
        ]
        assert [item.model_dump() for item in result.printed] == [
            {"value": "yes", "count": 0},
            {"value": "no", "count": 1},
        ]


class TestPrintStatistics:
    def test_print_statistics_invalid_period_defaults_to_30d(
        self, db_session: Session
    ) -> None:
        result = model_views.print_statistics(db_session, "not-a-real-period")
        assert result.period == "30d"
