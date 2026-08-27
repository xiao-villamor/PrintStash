"""Defends collection auto import creates models per member at the ingest API integration boundary.

A regression could publish an incomplete import or lose its durable job and artifact state.
"""

from __future__ import annotations

from ._url_zip_import_real_shared import (
    BENCHY_GCODE_A,
    BENCHY_STL,
    PRINTABLES_COLLECTION_URL,
    AsyncMock,
    Collection,
    Model,
    Path,
    Session,
    TestClient,
    _configure_storage,
    _fake_download_seq,
    _job,
    _requires,
    import_resolvers,
    patch,
    select,
)


@_requires(BENCHY_STL, BENCHY_GCODE_A)
def test_collection_auto_import_creates_models_per_member(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    """A collection URL (auto mode) imports every member into one collection,
    each model recording its own member page URL as source."""
    _configure_storage(tmp_path)
    members = [
        import_resolvers.CollectionMember(
            page_url="https://www.printables.com/model/1-a", title="A", source_id="1"
        ),
        import_resolvers.CollectionMember(
            page_url="https://www.printables.com/model/2-b", title="B", source_id="2"
        ),
    ]
    download = _fake_download_seq(
        [(BENCHY_STL.read_bytes(), "a.stl"), (BENCHY_GCODE_A.read_bytes(), "b.gcode")]
    )
    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        patch(
            "app.api.v1.ingest.import_resolvers.resolve_collection_url",
            new=AsyncMock(return_value=("Cool Stuff", members)),
        ),
        patch(
            "app.api.v1.ingest.import_resolvers.resolve_page_url",
            new=AsyncMock(return_value="https://files.printables.test/x"),
        ),
        patch("app.api.v1.ingest.importer.download_to_staging", new=download),
    ):
        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": PRINTABLES_COLLECTION_URL},
            ),
            auth_headers,
        )

    assert payload["state"] == "completed", payload
    result = payload["result"]
    assert result["kind"] == "collection_import"
    assert result["imported"] == 2
    # Both models live under the collection named after the source.
    collection = db_session.exec(
        select(Collection).where(Collection.path == "cool-stuff")
    ).first()
    assert collection is not None
    models = db_session.exec(
        select(Model).where(Model.collection_id == collection.id)
    ).all()
    assert {m.source_url for m in models} == {
        "https://www.printables.com/model/1-a",
        "https://www.printables.com/model/2-b",
    }


@_requires(BENCHY_STL)
def test_collection_review_then_select_imports_chosen_member(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    """Review mode returns a member manifest; selecting a subset imports only it."""
    _configure_storage(tmp_path)
    members = [
        import_resolvers.CollectionMember(
            page_url="https://www.printables.com/model/1-a", title="A", source_id="1"
        ),
        import_resolvers.CollectionMember(
            page_url="https://www.printables.com/model/2-b", title="B", source_id="2"
        ),
    ]
    with (
        patch(
            "app.api.v1.ingest.import_resolvers.resolve_collection_url",
            new=AsyncMock(return_value=("Cool Stuff", members)),
        ),
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
    ):
        manifest = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": PRINTABLES_COLLECTION_URL, "review": True},
            ),
            auth_headers,
        )

    result = manifest["result"]
    assert result["kind"] == "collection_manifest"
    assert [m["source_id"] for m in result["members"]] == ["1", "2"]
    token = result["collection_token"]

    download = _fake_download_seq([(BENCHY_STL.read_bytes(), "a.stl")])
    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        patch(
            "app.api.v1.ingest.import_resolvers.resolve_page_url",
            new=AsyncMock(return_value="https://files.printables.test/x"),
        ),
        patch("app.api.v1.ingest.importer.download_to_staging", new=download),
    ):
        payload = _job(
            client,
            client.post(
                f"/api/v1/ingest/collection/{token}/select",
                headers=auth_headers,
                json={"member_ids": ["1"]},
            ),
            auth_headers,
        )

    assert payload["state"] == "completed", payload
    assert payload["result"]["imported"] == 1
    models = db_session.exec(select(Model).where(Model.source_url.is_not(None))).all()
    assert any(m.source_url == "https://www.printables.com/model/1-a" for m in models)


@_requires(BENCHY_STL, BENCHY_GCODE_A)
def test_model_page_files_manifest_then_select(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    """A multi-file Printables page returns a file manifest; selecting a subset
    downloads only those files and imports them."""
    _configure_storage(tmp_path)
    files = [
        import_resolvers.ModelFile(file_id="10", name="a.stl", file_type="stl"),
        import_resolvers.ModelFile(file_id="11", name="b.stl", file_type="stl"),
        import_resolvers.ModelFile(file_id="12", name="c.stl", file_type="stl"),
    ]
    with (
        patch(
            "app.api.v1.ingest.import_resolvers.list_model_files",
            new=AsyncMock(return_value=("Springy Cat", files)),
        ),
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
    ):
        manifest = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": "https://www.printables.com/model/1660232-springy-cat"},
            ),
            auth_headers,
        )

    result = manifest["result"]
    assert result["kind"] == "model_files_manifest"
    assert result["page_title"] == "Springy Cat"
    assert len(result["files"]) == 3
    token = result["files_token"]

    download = _fake_download_seq(
        [(BENCHY_STL.read_bytes(), "a.stl"), (BENCHY_GCODE_A.read_bytes(), "b.gcode")]
    )
    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        patch(
            "app.api.v1.ingest.import_resolvers.resolve_selected_download",
            new=AsyncMock(
                return_value=[
                    "https://files.printables.test/a.stl",
                    "https://files.printables.test/b.stl",
                ]
            ),
        ),
        patch("app.api.v1.ingest.importer.download_to_staging", new=download),
    ):
        payload = _job(
            client,
            client.post(
                f"/api/v1/ingest/url/files/{token}/select",
                headers=auth_headers,
                json={"file_ids": ["10", "11"]},
            ),
            auth_headers,
        )

    assert payload["state"] == "completed", payload
    assert payload["result"]["imported"] == 2
    # Imported models carry the page URL (not the per-file link) as source.
    models = db_session.exec(select(Model).where(Model.source_url.is_not(None))).all()
    assert all(
        m.source_url == "https://www.printables.com/model/1660232-springy-cat"
        for m in models
    )
