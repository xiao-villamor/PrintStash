"""Defends download to staging fetches real file at the ingest API integration boundary.

A regression could publish an incomplete import or lose its durable job and artifact state.
"""

from __future__ import annotations

from ._url_zip_import_real_shared import (
    BENCHY_BGCODE,
    BENCHY_GCODE_A,
    BENCHY_GCODE_B,
    BENCHY_STL,
    MAKERWORLD_URL,
    PRINTABLES_URL,
    AsyncMock,
    Collection,
    File,
    FileType,
    Model,
    Path,
    Session,
    TestClient,
    _benchy_zip_bytes,
    _configure_storage,
    _fake_download,
    _job,
    _patch_resolver,
    _requires,
    _stage_bytes,
    importer,
    io,
    patch,
    select,
    zipfile,
)


@_requires(BENCHY_STL)
def test_import_real_benchy_from_printables_url_records_source(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
    staged = _stage_bytes(BENCHY_STL.read_bytes(), ".stl")

    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        # The Printables page resolves to a direct STL link server-side.
        _patch_resolver("https://files.printables.test/3dbenchy.stl"),
        patch(
            "app.api.v1.ingest.importer.download_to_staging",
            new=_fake_download(staged, "3dbenchy.stl"),
        ),
    ):
        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": PRINTABLES_URL},
            ),
            auth_headers,
        )

    assert payload["state"] == "completed", payload
    assert payload["model_id"] is not None
    model = db_session.get(Model, payload["model_id"])
    assert model is not None
    # The paste-able model-page URL is preserved verbatim as the source.
    assert model.source_url == PRINTABLES_URL

    file_row = db_session.exec(select(File).where(File.model_id == model.id)).first()
    assert file_row is not None and file_row.file_type == FileType.STL
    assert file_row.size_bytes == BENCHY_STL.stat().st_size
    # The staged copy was moved into the vault; the testdata original is intact.
    assert BENCHY_STL.exists()


@_requires(BENCHY_STL)
def test_import_from_url_names_model_from_download(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    """A URL import names the resulting model from the download's filename stem.

    URL imports no longer accept a ``model_name`` override — the name comes from
    the resolved file (or page) instead.
    """
    _configure_storage(tmp_path)
    staged = _stage_bytes(BENCHY_STL.read_bytes(), ".stl")

    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        _patch_resolver("https://files.printables.test/3dbenchy.stl"),
        patch(
            "app.api.v1.ingest.importer.download_to_staging",
            new=_fake_download(staged, "3dbenchy.stl"),
        ),
    ):
        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": PRINTABLES_URL},
            ),
            auth_headers,
        )

    assert payload["state"] == "completed", payload
    model = db_session.get(Model, payload["model_id"])
    assert model is not None
    assert model.name == "3dbenchy"


@_requires(BENCHY_STL, BENCHY_GCODE_A)
def test_import_real_benchy_zip_from_makerworld_url(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
    zip_bytes = _benchy_zip_bytes(BENCHY_STL, BENCHY_GCODE_A)
    staged = _stage_bytes(zip_bytes, ".zip")

    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        # The MakerWorld page resolves to a direct .zip bundle link server-side.
        _patch_resolver("https://makerworld.test/instance/123/f3mf.zip"),
        patch(
            "app.api.v1.ingest.importer.download_to_staging",
            new=_fake_download(staged, "3d-benchy.zip"),
        ),
    ):
        manifest = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": MAKERWORLD_URL},
            ),
            auth_headers,
        )

    # A .zip URL resolves to an archive manifest rather than importing directly.
    assert manifest["state"] == "completed", manifest
    result = manifest["result"]
    assert result["kind"] == "archive_manifest"
    archive_id = result["archive_id"]
    importable = sorted(e["name"] for e in result["entries"] if e["file_type"])
    assert importable == ["3DBenchy/3dbenchy.stl", "3DBenchy/3dbenchy_PLA_1h12m.gcode"]

    payload = _job(
        client,
        client.post(
            f"/api/v1/ingest/archive/{archive_id}/select",
            headers=auth_headers,
            json={"names": importable},
        ),
        auth_headers,
    )
    assert payload["state"] == "completed", payload
    assert payload["result"]["imported"] == 2

    # Both files landed as their own models, each carrying the MakerWorld URL.
    models = db_session.exec(
        select(Model).where(Model.source_url == MAKERWORLD_URL)
    ).all()
    assert len(models) == 2
    assert {FileType.STL, FileType.GCODE} == {
        db_session.exec(select(File).where(File.model_id == m.id)).first().file_type
        for m in models
    }


@_requires(BENCHY_GCODE_A, BENCHY_GCODE_B)
def test_import_zip_built_from_testdata_benchy_files(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    _configure_storage(tmp_path)
    # Include a binary .bgcode (now importable as G-code) and a README (skipped).
    sources = [BENCHY_GCODE_A, BENCHY_GCODE_B]
    expected = [
        "3DBenchy/3dbenchy_PLA_1h12m.gcode",
        "3DBenchy/3dbenchy_PLA_1h13m.gcode",
    ]
    if BENCHY_BGCODE.exists():
        sources.append(BENCHY_BGCODE)
        expected.append(f"3DBenchy/{BENCHY_BGCODE.name}")
    expected.sort()
    zip_bytes = _benchy_zip_bytes(*sources)

    manifest = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("benchy-bundle.zip", zip_bytes, "application/zip")},
    )
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()
    archive_id = body["archive_id"]
    importable = sorted(e["name"] for e in body["entries"] if e["file_type"])
    # The G-code files (including binary .bgcode) are importable; the .txt isn't.
    assert importable == expected

    payload = _job(
        client,
        client.post(
            f"/api/v1/ingest/archive/{archive_id}/select",
            headers=auth_headers,
            json={"names": importable},
        ),
        auth_headers,
    )
    assert payload["state"] == "completed", payload
    assert payload["result"]["imported"] == len(expected)

    # The g-code files sit under the zip's "3DBenchy/" folder, so they are
    # mirrored into a sub-collection nested beneath the archive's auto
    # collection ("benchy-bundle"), not flattened onto it.
    collection = db_session.exec(
        select(Collection).where(Collection.path == "benchy-bundle/3dbenchy")
    ).first()
    assert collection is not None
    models = db_session.exec(
        select(Model).where(Model.collection_id == collection.id)
    ).all()
    assert len(models) == len(expected)


@_requires(BENCHY_GCODE_A, BENCHY_GCODE_B)
def test_import_zip_mirrors_folder_structure_into_collections(
    tmp_path: Path,
    client: TestClient,
    db_session: Session,
    auth_headers: dict[str, str],
) -> None:
    """A zip with sub-folders mirrors its layout into nested sub-collections,
    while a file at the archive root stays in the archive's own collection."""
    _configure_storage(tmp_path)
    # Two distinct g-code files (distinct hashes → distinct models) arranged so
    # one sits at the root and one inside a "Terrain/" folder.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("root_benchy.gcode", BENCHY_GCODE_A.read_bytes())
        zf.writestr("Terrain/wall_benchy.gcode", BENCHY_GCODE_B.read_bytes())
    zip_bytes = buf.getvalue()

    manifest = client.post(
        "/api/v1/ingest/archive",
        headers=auth_headers,
        files={"file": ("structured-pack.zip", zip_bytes, "application/zip")},
    )
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()
    archive_id = body["archive_id"]
    names = sorted(e["name"] for e in body["entries"] if e["file_type"])
    # The manifest carries each entry's archive-relative path, not a bare name.
    assert names == ["Terrain/wall_benchy.gcode", "root_benchy.gcode"]

    payload = _job(
        client,
        client.post(
            f"/api/v1/ingest/archive/{archive_id}/select",
            headers=auth_headers,
            json={"names": names},
        ),
        auth_headers,
    )
    assert payload["state"] == "completed", payload
    assert payload["result"]["imported"] == 2

    def _models_in(path: str) -> list[Model]:
        coll = db_session.exec(
            select(Collection).where(Collection.path == path)
        ).first()
        assert coll is not None, f"missing collection {path!r}"
        return list(
            db_session.exec(select(Model).where(Model.collection_id == coll.id)).all()
        )

    # The root-level file lands directly in the archive's auto collection...
    assert len(_models_in("structured-pack")) == 1
    # ...and the "Terrain/" folder becomes a nested sub-collection beneath it.
    assert len(_models_in("structured-pack/terrain")) == 1


def test_import_url_unrecognised_host_html_fails_gracefully(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """A direct URL on an unrecognised host that resolves to HTML (no usable
    suffix) is rejected cleanly as ``url_not_a_direct_file`` rather than
    crashing the job. Resolution returns ``None`` for the unknown host, so the
    raw URL is downloaded as-is."""
    _configure_storage(tmp_path)
    staged = _stage_bytes(b"<!doctype html><title>3DBenchy</title>", ".bin")

    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        _patch_resolver(None),  # unrecognised host -> treated as a direct URL
        patch(
            "app.api.v1.ingest.importer.download_to_staging",
            new=_fake_download(staged, "some-page"),
        ),
    ):
        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": "https://example.test/some-page"},
            ),
            auth_headers,
        )

    assert payload["state"] == "failed", payload
    assert payload["error"] == "url_not_a_direct_file"
    # The unusable download was cleaned out of staging.
    assert not staged.exists()


def test_import_url_unresolvable_page_reports_host_error(
    tmp_path: Path,
    client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """When a recognised model page can't be resolved to a download link, the
    job fails with the host-specific code (not a generic crash), and nothing is
    downloaded."""
    _configure_storage(tmp_path)

    download = _fake_download(_stage_bytes(b"x", ".bin"), "unused")
    with (
        patch("app.api.v1.ingest.importer.validate_public_url", return_value=None),
        patch(
            "app.api.v1.ingest.import_resolvers.resolve_page_url",
            new=AsyncMock(
                side_effect=importer.ImportError_("printables_resolve_failed")
            ),
        ),
        patch(
            "app.api.v1.ingest.import_resolvers.list_model_files",
            new=AsyncMock(return_value=None),
        ),
        patch("app.api.v1.ingest.importer.download_to_staging", new=download),
    ):
        payload = _job(
            client,
            client.post(
                "/api/v1/ingest/url",
                headers=auth_headers,
                json={"url": PRINTABLES_URL},
            ),
            auth_headers,
        )

    assert payload["state"] == "failed", payload
    assert payload["error"] == "printables_resolve_failed"
    # Resolution failed before any download was attempted.
    download.assert_not_awaited()
