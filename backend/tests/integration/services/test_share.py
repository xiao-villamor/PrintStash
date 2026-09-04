"""What a share token is worth, decided in one place.

`resolve_share` is the gate every public handler passes through, and its job is to
collapse *every* failure into the same 404: a token that never existed, one that was
revoked, one that expired, and one whose model has since been trashed all look identical
from outside. Anything that distinguishes them turns the public surface into an oracle
for what the vault contains.

The other half is scoping. A link may name specific G-code revisions; the ids are
validated against *that model's live G-code* at creation, so a link can never be minted
that points at another model's file or at a trashed one.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

from app.core.time import ensure_utc, utcnow
from app.db.models import File, FileType, Model, ShareLink
from app.services import share
from tests.factories import build_file, build_model


def _model(db_session: Session, slug: str, **fields) -> Model:
    row = build_model(
        db_session, name=slug.title(), slug=slug, hash=f"{slug:h<64}"[:64], **fields
    )
    db_session.refresh(row)
    return row


def _gcode(db_session: Session, model: Model, name: str, **fields) -> File:
    used = len(db_session.exec(select(File).where(File.model_id == model.id)).all())
    row = build_file(
        db_session,
        model,
        path=f"{name}",
        filename=name,
        file_type=FileType.GCODE,
        version=used + 1,  # (model_id, version) is unique
        size_bytes=10,
        sha256=f"{name:a<64}"[:64],
        **fields,
    )
    db_session.refresh(row)
    return row


def _create(db_session: Session, model: Model, **overrides):
    """`create_share` with the router's defaults filled in."""
    kwargs: dict = {
        "model_id": model.id,
        "expires_in_days": 7,
        "allow_download": False,
        "created_by": None,
    }
    kwargs.update(overrides)
    return share.create_share(db_session, **kwargs)


class TestCreateShare:
    def test_returns_a_token_that_is_not_stored(self, db_session: Session) -> None:
        model = _model(db_session, "minted")

        link, raw_token = _create(db_session, model)

        assert raw_token
        # Only the hash is persisted, so a database leak does not hand over live links.
        assert raw_token not in (link.token_hash or "")

    @pytest.mark.parametrize(
        ("requested", "expected_days"),
        [
            pytest.param(0, 1, id="below-min-clamps-to-one-day"),
            pytest.param(400, 365, id="above-max-clamps-to-a-year"),
            pytest.param(7, 7, id="in-range-kept"),
        ],
    )
    def test_clamps_the_lifetime(
        self, db_session: Session, requested: int, expected_days: int
    ) -> None:
        model = _model(db_session, f"lifetime{requested}")

        link, _ = _create(db_session, model, expires_in_days=requested)

        assert link.expires_at is not None
        # SQLite hands the column back naive; compare on the same footing.
        actual = (ensure_utc(link.expires_at) - utcnow()).days
        assert actual in (expected_days - 1, expected_days)

    def test_scopes_the_link_to_the_named_revisions(self, db_session: Session) -> None:
        model = _model(db_session, "scoped")
        chosen = _gcode(db_session, model, "chosen.gcode")
        _gcode(db_session, model, "other.gcode")

        link, _ = _create(db_session, model, revision_file_ids=[chosen.id])

        assert share._selected_file_ids(link) == [chosen.id]

    def test_ignores_a_repeated_revision_id(self, db_session: Session) -> None:
        model = _model(db_session, "deduped")
        chosen = _gcode(db_session, model, "chosen.gcode")

        link, _ = _create(db_session, model, revision_file_ids=[chosen.id, chosen.id])

        assert share._selected_file_ids(link) == [chosen.id]

    def test_refuses_an_empty_revision_selection(self, db_session: Session) -> None:
        model = _model(db_session, "empty-selection")

        with pytest.raises(HTTPException) as raised:
            _create(db_session, model, revision_file_ids=[])

        assert raised.value.status_code == 400
        assert raised.value.detail == "no_revisions_selected"

    def test_refuses_a_revision_from_another_model(self, db_session: Session) -> None:
        model = _model(db_session, "mine")
        other = _model(db_session, "theirs")
        stranger = _gcode(db_session, other, "stranger.gcode")

        with pytest.raises(HTTPException) as raised:
            _create(db_session, model, revision_file_ids=[stranger.id])

        assert raised.value.status_code == 400
        assert raised.value.detail == "invalid_revision_file_id"

    def test_refuses_a_trashed_revision(self, db_session: Session) -> None:
        model = _model(db_session, "trashed-revision")
        gone = _gcode(db_session, model, "gone.gcode", deleted_at=utcnow())

        with pytest.raises(HTTPException) as raised:
            _create(db_session, model, revision_file_ids=[gone.id])

        assert raised.value.detail == "invalid_revision_file_id"


class TestSelectedFileIds:
    def test_reports_no_scope_when_none_was_set(self, db_session: Session) -> None:
        model = _model(db_session, "unscoped")
        link, _ = _create(db_session, model)

        assert share._selected_file_ids(link) is None

    @pytest.mark.parametrize(
        "stored",
        [
            pytest.param("not json at all", id="unparseable"),
            pytest.param('{"file": 1}', id="not-a-list"),
        ],
    )
    def test_treats_unreadable_stored_scope_as_no_scope(
        self, db_session: Session, stored: str
    ) -> None:
        model = _model(db_session, f"corrupt{len(stored)}")
        link, _ = _create(db_session, model)
        link.selected_file_ids_json = stored

        # A corrupt column must not crash the public view; it degrades to "all files".
        assert share._selected_file_ids(link) is None


class TestResolveShare:
    def test_returns_the_link_for_a_live_token(self, db_session: Session) -> None:
        model = _model(db_session, "live")
        link, raw_token = _create(db_session, model)

        assert share.resolve_share(db_session, raw_token).id == link.id

    @pytest.mark.parametrize(
        "token", [pytest.param("", id="empty"), pytest.param("nope", id="unknown")]
    )
    def test_rejects_a_token_that_was_never_real(
        self, db_session: Session, token: str
    ) -> None:
        with pytest.raises(HTTPException) as raised:
            share.resolve_share(db_session, token)

        assert raised.value.status_code == 404
        assert raised.value.detail == "not_found"

    def test_rejects_a_revoked_token(self, db_session: Session) -> None:
        model = _model(db_session, "revoked")
        link, raw_token = _create(db_session, model)
        share.revoke_share(db_session, link)

        with pytest.raises(HTTPException) as raised:
            share.resolve_share(db_session, raw_token)

        assert raised.value.detail == "not_found"

    def test_rejects_an_expired_token(self, db_session: Session) -> None:
        model = _model(db_session, "expired")
        link, raw_token = _create(db_session, model)
        link.expires_at = utcnow() - timedelta(days=1)
        db_session.add(link)
        db_session.commit()

        with pytest.raises(HTTPException) as raised:
            share.resolve_share(db_session, raw_token)

        assert raised.value.detail == "not_found"

    def test_rejects_a_token_whose_model_was_trashed(self, db_session: Session) -> None:
        model = _model(db_session, "trashed-model")
        _, raw_token = _create(db_session, model)
        model.deleted_at = utcnow()
        db_session.add(model)
        db_session.commit()

        with pytest.raises(HTTPException) as raised:
            share.resolve_share(db_session, raw_token)

        # Same 404 as an unknown token: the public surface is not an oracle.
        assert raised.value.detail == "not_found"


class TestShareFileOr404:
    def test_returns_a_file_of_the_shared_model(self, db_session: Session) -> None:
        model = _model(db_session, "reachable")
        gcode = _gcode(db_session, model, "reachable.gcode")
        link, _ = _create(db_session, model)

        assert share.share_file_or_404(db_session, link, gcode.id).id == gcode.id

    def test_refuses_a_file_of_another_model(self, db_session: Session) -> None:
        model = _model(db_session, "mine2")
        other = _model(db_session, "theirs2")
        stranger = _gcode(db_session, other, "stranger2.gcode")
        link, _ = _create(db_session, model)

        with pytest.raises(HTTPException) as raised:
            share.share_file_or_404(db_session, link, stranger.id)

        assert raised.value.status_code == 404


class TestRecordAccess:
    def test_counts_each_view(self, db_session: Session) -> None:
        model = _model(db_session, "counted-service")
        link, _ = _create(db_session, model)

        share.record_access(db_session, link)
        share.record_access(db_session, link)

        assert db_session.get(ShareLink, link.id).access_count == 2


class TestRevokeShare:
    def test_stamps_the_revocation(self, db_session: Session) -> None:
        model = _model(db_session, "revocable-service")
        link, _ = _create(db_session, model)

        revoked = share.revoke_share(db_session, link)

        assert revoked.revoked_at is not None
