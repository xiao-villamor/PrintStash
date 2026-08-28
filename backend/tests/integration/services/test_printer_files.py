"""Deciding whether a file on the printer is one of ours, without guessing.

A printer's storage contains files PrintStash uploaded and files the user put
there by SD card. Matching them back to library artifacts is what makes "already
on this printer" work — and a wrong match is worse than no match, because it
attributes somebody's print to the wrong model and then offers to delete the
wrong bytes.

So the matching is a strict ladder, and this file pins the order:

1. **Upload history** — we recorded sending this exact file. Strongest evidence.
2. **A vault marker** embedded in the G-code. Ours by construction.
3. **Filename**, last and weakest, because two models can produce `cube.gcode`.

The refusals matter as much as the ladder. A marker that is present but does not
match reports a *mismatch* rather than falling through to filename — falling
through would silently accept the weaker evidence exactly when the stronger
evidence says no. And an externally-started job is never treated as upload
history, since we did not send it and cannot claim what it printed.

A file that stops appearing is marked missing rather than deleted: the printer
may simply be offline, and forgetting the association would lose the link.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, select

from app.db.models import File, Model, PrinterFile
from app.services import printer_files as pf
from app.services.printer_files import (
    build_traceable_remote_filename,
    sync_printer_files,
)
from tests.factories import build_file, build_model, build_print_job, build_printer


class _FakeFile:
    """Minimal stand-in for File for pure filename/marker tests."""

    def __init__(self, id: int, original_filename: str, sha256: str) -> None:
        self.id = id
        self.original_filename = original_filename
        self.sha256 = sha256


class TestTraceableFilename:
    def test_marker_round_trips_through_regex(self) -> None:
        f = _FakeFile(42, "My Cool Part.gcode", "abcdef0123456789" * 4)
        name = build_traceable_remote_filename(f)
        match = pf._VAULT_MARKER_RE.search(name)
        assert match is not None
        assert match.group("file_id") == "42"
        assert f.sha256.lower().startswith(match.group("sha").lower())

    def test_unsafe_characters_are_sanitised(self) -> None:
        f = _FakeFile(1, "../weird name!@#.gcode", "a" * 64)
        name = build_traceable_remote_filename(f)
        # Stem keeps only [A-Za-z0-9._-]; no path separators or specials survive.
        assert "/" not in name and "!" not in name and "@" not in name
        assert pf._VAULT_MARKER_RE.search(name) is not None

    def test_empty_stem_falls_back_to_print(self) -> None:
        f = _FakeFile(7, "....gcode", "deadbeef" * 8)
        assert build_traceable_remote_filename(f).startswith("print__vault-f7-")

    def test_non_gcode_suffix_is_coerced(self) -> None:
        f = _FakeFile(9, "model.stl", "cafe" * 16)
        assert build_traceable_remote_filename(f).endswith(".gcode")

    def test_long_stem_is_truncated_within_limit(self) -> None:
        f = _FakeFile(123, "x" * 1000 + ".gcode", "b" * 64)
        name = build_traceable_remote_filename(f)
        assert len(name) <= 512
        assert pf._VAULT_MARKER_RE.search(name) is not None

    def test_trailing_marker_wins_over_decoy_in_stem(self) -> None:
        # A user-chosen stem that itself looks like a marker must not be matched
        # ahead of the genuine trailing marker.
        spoof = "vault-f99-deadbeef12__vault-f42-abcdef012345.gcode"
        match = pf._VAULT_MARKER_RE.search(spoof)
        assert match is not None
        assert match.group("file_id") == "42"

    def test_bare_lookalike_without_delimiter_is_not_a_marker(self) -> None:
        # No "__" delimiter => not a vault-generated marker (e.g. an external
        # file a user happened to name this way).
        assert pf._VAULT_MARKER_RE.search("vault-f99-deadbeef12.gcode") is None

    def test_build_from_lookalike_stem_still_matches_real_id(self) -> None:
        f = _FakeFile(7, "vault-f99-deadbeef12.gcode", "cafe" * 16)
        match = pf._VAULT_MARKER_RE.search(build_traceable_remote_filename(f))
        assert match is not None and match.group("file_id") == "7"


class TestRemoteFieldParsers:
    def test_remote_name_prefers_the_path_with_no_leading_slash(self) -> None:
        assert pf._remote_name({"path": "/sub/a.gcode"}) == "sub/a.gcode"
        assert pf._remote_name({"filename": "b.gcode"}) == "b.gcode"
        assert pf._remote_name({"name": "  c.gcode  "}) == "c.gcode"
        assert pf._remote_name({}) is None

    def test_remote_size_coerces_whatever_the_printer_reported(self) -> None:
        assert pf._remote_size({"size": "456"}) == 456
        assert pf._remote_size({"size_bytes": 789}) == 789
        assert pf._remote_size({"size": "not-a-number"}) is None
        assert pf._remote_size({}) is None

    def test_remote_modified_is_utc_aware(self) -> None:
        # Regression: a UTC Unix timestamp must come back as aware UTC, not a
        # server-local naive datetime.
        got = pf._remote_modified({"modified": 1700000000})
        assert got == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)
        assert got.tzinfo is not None

    def test_remote_modified_is_none_for_an_unusable_timestamp(self) -> None:
        assert pf._remote_modified({}) is None
        assert pf._remote_modified({"modified": "bad"}) is None


def _make_gcode(
    session: Session,
    *,
    name: str = "part.gcode",
    size: int = 123,
    model_slug: str = "part",
) -> tuple[Model, File]:
    model = build_model(
        session, name=model_slug.title(), slug=model_slug, hash=model_slug[0] * 64
    )
    f = build_file(
        session,
        model,
        path=f"/data/{name}",
        filename=name,
        file_type="gcode",
        version=1,
        size_bytes=size,
        sha256="f" * 64,
    )
    return model, f


class TestSyncPrinterFiles:
    def test_sync_matches_upload_history_first(self, db_session: Session):
        _, f = _make_gcode(db_session)
        printer = build_printer(
            db_session, name="Ender", moonraker_url="http://10.0.0.1:7125"
        )
        build_print_job(
            db_session,
            f,
            printer_id=printer.id,
            remote_filename="folder/custom-name.gcode",
        )

        rows = sync_printer_files(
            db_session,
            printer_id=printer.id,
            remote_files=[{"path": "folder/custom-name.gcode", "size": 999}],
        )

        assert len(rows) == 1
        assert rows[0].file_id == f.id
        assert rows[0].matched_by == "upload_history"

    def test_sync_matches_vault_marker_before_filename(self, db_session: Session):
        _, marked = _make_gcode(
            db_session,
            name="same-name.gcode",
            size=111,
            model_slug="marked",
        )
        _, newer_same_name = _make_gcode(
            db_session,
            name="same-name.gcode",
            size=222,
            model_slug="newer",
        )
        printer = build_printer(
            db_session, name="Ender", moonraker_url="http://10.0.0.1:7125"
        )

        remote_filename = build_traceable_remote_filename(marked)
        rows = sync_printer_files(
            db_session,
            printer_id=printer.id,
            remote_files=[
                {
                    "path": f"subdir/{remote_filename}",
                    "size": newer_same_name.size_bytes,
                }
            ],
        )

        assert rows[0].file_id == marked.id
        assert rows[0].matched_by == "vault_marker"

    def test_sync_reports_marker_mismatch_without_guessing(self, db_session: Session):
        _, f = _make_gcode(db_session)
        printer = build_printer(
            db_session, name="Ender", moonraker_url="http://10.0.0.1:7125"
        )

        rows = sync_printer_files(
            db_session,
            printer_id=printer.id,
            remote_files=[
                {
                    "path": f"part__vault-f{f.id}-{'0' * 12}.gcode",
                    "size": f.size_bytes,
                }
            ],
        )

        assert rows[0].file_id is None
        assert rows[0].matched_by == "vault_marker_mismatch"

    def test_sync_does_not_match_external_job_as_upload_history(
        self, db_session: Session
    ):
        _, f = _make_gcode(db_session)
        printer = build_printer(
            db_session, name="Ender", moonraker_url="http://10.0.0.1:7125"
        )
        build_print_job(
            db_session,
            f,
            printer_id=printer.id,
            remote_filename="external-name.gcode",
            source="external",
        )

        rows = sync_printer_files(
            db_session,
            printer_id=printer.id,
            remote_files=[{"path": "external-name.gcode", "size": 999}],
        )

        assert rows[0].file_id is None
        assert rows[0].matched_by == "external"

    def test_sync_matches_filename_then_marks_missing(self, db_session: Session):
        _, f = _make_gcode(db_session, name="bracket.gcode", size=456)
        printer = build_printer(
            db_session, name="Ender", moonraker_url="http://10.0.0.1:7125"
        )

        rows = sync_printer_files(
            db_session,
            printer_id=printer.id,
            remote_files=[{"path": "subdir/bracket.gcode", "size": 456}],
        )
        assert rows[0].file_id == f.id
        assert rows[0].matched_by == "filename"
        assert rows[0].missing_since is None

        rows = sync_printer_files(db_session, printer_id=printer.id, remote_files=[])
        assert rows[0].missing_since is not None

    def test_sync_keeps_unmatched_external_file(self, db_session: Session):
        printer = build_printer(
            db_session, name="Ender", moonraker_url="http://10.0.0.1:7125"
        )

        sync_printer_files(
            db_session,
            printer_id=printer.id,
            remote_files=[{"path": "external.gcode", "size": 789}],
        )

        row = db_session.exec(select(PrinterFile)).one()
        assert row.file_id is None
        assert row.matched_by == "external"
