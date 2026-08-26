"""Defends ownership snapshot collection embedded image id must match row at the services vault audit integration boundary.

A regression could miss corruption or repair ownership and metadata incorrectly.
"""

from __future__ import annotations

from ._vault_audit_internals_shared import (
    Collection,
    Session,
    _make_file,
    _make_model,
    all_owned_blob_keys,
    get_backend,
    ownership_snapshot,
)


def test_ownership_snapshot_collection_embedded_image_id_must_match_row(
    db_session: Session,
) -> None:
    other = Collection(name="other-col", slug="other-col", path="other-col")
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    owner = Collection(
        name="owner-col",
        slug="owner-col",
        path="owner-col",
        readme=f"![pic](/collections/{other.id}/images/stolen.png)",
    )
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)

    result = ownership_snapshot(db_session, discover=False)

    stolen_key = get_backend().collection_image_key(other.id, "stolen.png")
    assert stolen_key not in {blob.key for blob in result.embedded}

    owner.readme = f"![pic](/collections/{owner.id}/images/mine.png)"
    db_session.add(owner)
    db_session.commit()

    result2 = ownership_snapshot(db_session, discover=False)
    matching = [
        blob
        for blob in result2.embedded
        if blob.resource_type == "collection_image" and blob.resource_id == owner.id
    ]
    assert len(matching) == 1
    assert matching[0].key == get_backend().collection_image_key(owner.id, "mine.png")


def test_all_owned_blob_keys_includes_primary_and_external_files(
    db_session: Session,
) -> None:
    model = _make_model(db_session, "owned-keys")
    internal = _make_file(db_session, model, path="internal.stl")
    external = _make_file(
        db_session,
        model,
        path="/nas/external.stl",
        is_external=True,
        version=2,
    )

    keys = all_owned_blob_keys(db_session)

    assert internal.path in keys
    assert external.path in keys
