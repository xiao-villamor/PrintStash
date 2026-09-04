"""Compatibility service tests for the legacy model part-option groups."""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from app.core.time import utcnow
from app.db.models import FileType, PartGroup, PartOption
from app.schemas.models import PartGroupWrite, PartOptionWrite
from app.services import part_options


def option(
    name: str,
    *,
    file_id: int | None = None,
    model_id: int | None = None,
    is_default: bool = False,
) -> PartOptionWrite:
    return PartOptionWrite(
        file_id=file_id,
        model_id=model_id,
        name=name,
        is_default=is_default,
    )


def group(name: str, *options: PartOptionWrite) -> PartGroupWrite:
    return PartGroupWrite(name=name, options=list(options))


class TestPartOptionService:
    def test_reads_revision_counts_for_source_choice(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Parent")
        short = make_file(model, file_type=FileType.STL, filename="short.stl")
        long = make_file(model, file_type=FileType.THREE_MF, filename="long.3mf")
        gcode = make_file(model, file_type=FileType.GCODE, filename="parent.gcode")

        result = part_options.replace_for_model(
            db_session,
            model.id,
            [
                group(
                    " Handle ",
                    option("Short", file_id=short.id, is_default=True),
                    option("Long", file_id=long.id),
                )
            ],
        )

        assert result[0].name == "Handle"
        assert result[0].options[0].model.source_file_count == 2
        assert result[0].options[0].model.gcode_revision_count == 1
        assert result[0].options[0].model.thumbnail_url is None
        assert gcode.id not in {item.file_id for item in result[0].options}

    def test_read_callback_supplies_member_thumbnail(
        self, db_session: Session, make_model, make_file
    ) -> None:
        parent = make_model("Parent")
        member = make_model("Member")
        source = make_file(parent, filename="member.stl")
        part_options.replace_for_model(
            db_session,
            parent.id,
            [group("Part", option("Member", file_id=source.id, is_default=True))],
        )

        result = part_options.read_for_model(
            db_session,
            parent.id,
            thumbnail_url_for=lambda row: f"/thumb/{row.slug}",
        )

        assert result[0].options[0].model.thumbnail_url == f"/thumb/{parent.slug}"
        assert member.id not in {item.model.id for item in result[0].options}

    def test_read_hides_group_with_trashed_legacy_file(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Incomplete parent")
        source = make_file(model, filename="part.stl")
        part_options.replace_for_model(
            db_session,
            model.id,
            [group("Part", option("Part", file_id=source.id, is_default=True))],
        )
        source.deleted_at = utcnow()
        db_session.add(source)
        db_session.commit()

        assert part_options.read_for_model(db_session, model.id) == []

    def test_remove_missing_file_is_a_noop(self, db_session: Session) -> None:
        part_options.remove_file_from_groups(db_session, 99999999)
        part_options.remove_model_from_groups(db_session, 99999999)

    def test_remove_option_without_group_deletes_orphan(
        self, db_session: Session, make_model, make_file, monkeypatch
    ) -> None:
        model = make_model("Orphan parent")
        source = make_file(model, filename="orphan.stl")
        parent = PartGroup(
            model_id=model.id, name="Part", name_key="part", sort_order=0
        )
        db_session.add(parent)
        db_session.commit()
        db_session.refresh(parent)
        orphan = PartOption(
            part_group_id=parent.id,
            file_id=source.id,
            name="Orphan",
            name_key="orphan",
            sort_order=0,
            is_default=True,
        )
        db_session.add(orphan)
        db_session.commit()
        monkeypatch.setattr(db_session, "get", lambda *_args, **_kwargs: None)

        part_options._remove_option(db_session, orphan)

        assert db_session.get(PartOption, orphan.id) is None

    def test_remove_sole_option_removes_group(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Sole parent")
        source = make_file(model, filename="sole.stl")
        part_options.replace_for_model(
            db_session,
            model.id,
            [group("Part", option("Sole", file_id=source.id, is_default=True))],
        )
        part_options.remove_file_from_groups(db_session, source.id)
        db_session.commit()

        assert db_session.exec(select(PartGroup)).all() == []

    def test_remove_default_promotes_first_sibling(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Promoting parent")
        first = make_file(model, filename="first.stl")
        second = make_file(model, filename="second.stl")
        part_options.replace_for_model(
            db_session,
            model.id,
            [
                group(
                    "Part",
                    option("First", file_id=first.id, is_default=True),
                    option("Second", file_id=second.id),
                )
            ],
        )
        part_options.remove_file_from_groups(db_session, first.id)
        db_session.commit()

        replacement = db_session.exec(
            select(PartOption).where(PartOption.file_id == second.id)
        ).one()
        assert replacement.is_default is True

    def test_rejects_duplicate_group_names(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Duplicate groups")
        first = make_file(model, filename="first.stl")
        second = make_file(model, filename="second.stl")

        with pytest.raises(
            part_options.PartOptionsError, match="part_group_name_duplicate"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [
                    group("Handle", option("First", file_id=first.id, is_default=True)),
                    group(
                        " handle ", option("Second", file_id=second.id, is_default=True)
                    ),
                ],
            )

    def test_rejects_blank_group_name(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Blank group")
        source = make_file(model, filename="blank.stl")

        with pytest.raises(
            part_options.PartOptionsError, match="part_group_name_duplicate"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [group("   ", option("Source", file_id=source.id, is_default=True))],
            )

    def test_rejects_duplicate_option_names(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Duplicate options")
        first = make_file(model, filename="first.stl")
        second = make_file(model, filename="second.stl")

        with pytest.raises(
            part_options.PartOptionsError, match="part_option_name_duplicate"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [
                    group(
                        "Part",
                        option("Same", file_id=first.id, is_default=True),
                        option(" same ", file_id=second.id),
                    )
                ],
            )

    def test_rejects_duplicate_artifact_targets(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Duplicate targets")
        source = make_file(model, filename="source.stl")
        with pytest.raises(
            part_options.PartOptionsError, match="artifact_already_part_option"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [
                    group(
                        "Part",
                        option("First", file_id=source.id, is_default=True),
                        option("Second", file_id=source.id),
                    )
                ],
            )

    def test_rejects_duplicate_model_targets(
        self, db_session: Session, make_model
    ) -> None:
        model = make_model("Duplicate model targets")
        member = make_model("Member")
        with pytest.raises(
            part_options.PartOptionsError, match="model_already_part_option"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [
                    group(
                        "Part",
                        option("First", model_id=member.id, is_default=True),
                        option("Second", model_id=member.id),
                    )
                ],
            )

    def test_rejects_missing_artifact(self, db_session: Session, make_model) -> None:
        model = make_model("Artifact validation")
        with pytest.raises(
            part_options.PartOptionsError, match="part_option_artifact_not_found"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [group("Part", option("Missing", file_id=99999999, is_default=True))],
            )

    def test_rejects_non_source_artifact(
        self, db_session: Session, make_model, make_file
    ) -> None:
        model = make_model("Source validation")
        gcode = make_file(model, filename="part.gcode", file_type=FileType.GCODE)
        with pytest.raises(
            part_options.PartOptionsError, match="part_option_artifact_not_source"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [group("Part", option("G-code", file_id=gcode.id, is_default=True))],
            )

    def test_rejects_missing_model(self, db_session: Session, make_model) -> None:
        model = make_model("Model validation")
        with pytest.raises(
            part_options.PartOptionsError, match="part_option_model_not_found"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [group("Part", option("Missing", model_id=99999999, is_default=True))],
            )

    def test_rejects_multiple_defaults(self, db_session: Session, make_model) -> None:
        model = make_model("Default validation")
        member = make_model("Member")
        with pytest.raises(
            part_options.PartOptionsError, match="part_group_default_required"
        ):
            part_options.replace_for_model(
                db_session,
                model.id,
                [
                    group(
                        "Part",
                        option("First", model_id=member.id, is_default=True),
                        option(
                            "Second", model_id=make_model("Other").id, is_default=True
                        ),
                    )
                ],
            )

    def test_rejects_model_already_used_by_another_parent(
        self, db_session: Session, make_model
    ) -> None:
        first = make_model("First parent")
        second = make_model("Second parent")
        member = make_model("Shared member")
        part_options.replace_for_model(
            db_session,
            first.id,
            [group("Part", option("Member", model_id=member.id, is_default=True))],
        )

        with pytest.raises(
            part_options.PartOptionsError, match="model_already_part_option"
        ):
            part_options.replace_for_model(
                db_session,
                second.id,
                [group("Part", option("Member", model_id=member.id, is_default=True))],
            )

    def test_rejects_nested_model_cycle(self, db_session: Session, make_model) -> None:
        first = make_model("Cycle first")
        second = make_model("Cycle second")
        part_options.replace_for_model(
            db_session,
            first.id,
            [group("Part", option("Second", model_id=second.id, is_default=True))],
        )

        with pytest.raises(part_options.PartOptionsError, match="part_option_cycle"):
            part_options.replace_for_model(
                db_session,
                second.id,
                [group("Part", option("First", model_id=first.id, is_default=True))],
            )

    def test_member_graph_ignores_self_reference(
        self, db_session: Session, make_model
    ) -> None:
        model = make_model("Self graph")
        parent = PartGroup(
            model_id=model.id, name="Part", name_key="part", sort_order=0
        )
        db_session.add(parent)
        db_session.commit()
        db_session.refresh(parent)
        db_session.add(
            PartOption(
                part_group_id=parent.id,
                model_id=model.id,
                name="Self",
                name_key="self",
                sort_order=0,
                is_default=True,
            )
        )
        db_session.commit()

        assert part_options._member_graph(db_session) == {}
