"""Storage contracts the ORM layer must keep with the migrations that built it.

A SQLModel table declaration and an Alembic migration describe the same column
twice, in two places, and nothing checks that the two descriptions agree. Where
they disagree the mismatch is invisible until a real row is read back — and then
it is a 500 on a listing endpoint, not a validation error at write time.

Enum columns are the sharp case, because SQLAlchemy stores the enum *member name*
rather than its value. `ExternalLibraryWatchMode.AUTO` is written as `"AUTO"`, so
a migration whose `server_default` is the lowercase value writes rows the ORM
cannot read: every pre-existing library raises `LookupError` on load and the
libraries listing 500s for exactly the installations that upgraded. Asserting on
the raw stored string is the only way to see it, since reading through the ORM
round-trips the value and hides the disagreement.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session, select

from app.db.models import (
    ExternalLibraryWatchMode,
    InboxItem,
    InboxItemResult,
    InboxItemResultState,
)
from tests.factories import build_external_library, build_user


class TestExternalLibrary:
    def test_stores_the_watch_mode_as_the_enum_member_name(
        self, tmp_path: Path, db_session: Session
    ) -> None:
        nas = tmp_path / "nas"
        nas.mkdir()

        library = build_external_library(
            db_session, nas, name="nas", watch_mode=ExternalLibraryWatchMode.AUTO
        )

        raw = db_session.execute(
            text("SELECT watch_mode FROM external_libraries WHERE id = :id"),
            {"id": library.id},
        ).scalar_one()
        assert raw == "AUTO", "migration server_default must match the member name"


class TestInboxItemResult:
    def test_stores_the_result_state_as_the_enum_value(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "provenance-owner")
        item = InboxItem(owner_user_id=user.id)
        db_session.add(item)
        db_session.flush()
        for index, state in enumerate(InboxItemResultState):
            db_session.add(
                InboxItemResult(
                    inbox_item_id=item.id,
                    source_selection_id=f"selection-{index}",
                    result_key=f"key-{index}",
                    original_filename="part.stl",
                    state=state,
                )
            )

        db_session.commit()

        # The opposite convention from `ExternalLibraryWatchMode` above, and
        # deliberately so: this column is declared to store values, and the API
        # serialises the stored string straight out. Storing member names here
        # would put "IMPORTED" in a response body the frontend matches on.
        rows = db_session.exec(select(InboxItemResult)).all()
        assert [row.state for row in rows] == ["imported", "deduplicated", "failed"]
