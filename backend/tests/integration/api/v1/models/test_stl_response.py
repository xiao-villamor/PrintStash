"""Defends stl response at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._cross_unit_shared import (
    File,
    Path,
    SimpleNamespace,
    _make_model,
)


def test_stl_response_streams_raw_stl_without_buffering(db_session, tmp_path):
    from starlette.responses import FileResponse, Response

    from app.api.v1 import files as files_api
    from app.core.config import _overlay
    from app.services.storage_backend import get_backend

    _overlay["data_dir"] = tmp_path / "files"
    backend = get_backend()
    blob = tmp_path / "files" / "raw.stl"
    data = b"solid raw\n" + b"x" * 4096 + b"\nendsolid raw\n"
    backend.write_bytes(data, str(blob))

    m = _make_model(db_session, slug="rawstl", hash_="f" * 64)
    f = File(
        model_id=m.id,
        path=str(blob),
        original_filename="raw.stl",
        file_type="stl",
        version=1,
        size_bytes=len(data),
        sha256="b" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)

    request = SimpleNamespace(headers={})
    res = files_api.stl_response(f, request)

    # Local backend hands back a real path, so the blob is streamed off disk via
    # FileResponse rather than slurped into an in-memory Response body.
    assert isinstance(res, FileResponse)
    assert not (isinstance(res, Response) and getattr(res, "body", None))
    assert res.media_type == "application/sla"
    assert res.headers["ETag"] == f'"{f.sha256}"'
    assert "raw.stl" in res.headers["content-disposition"]
    assert Path(res.path).read_bytes() == data


def test_stl_response_honours_if_none_match(db_session, tmp_path):
    from app.api.v1 import files as files_api
    from app.core.config import _overlay
    from app.services.storage_backend import get_backend

    _overlay["data_dir"] = tmp_path / "files"
    blob = tmp_path / "files" / "etag.stl"
    get_backend().write_bytes(b"solid etag\nendsolid etag\n", str(blob))

    m = _make_model(db_session, slug="etag", hash_="e" * 64)
    f = File(
        model_id=m.id,
        path=str(blob),
        original_filename="etag.stl",
        file_type="stl",
        version=1,
        size_bytes=24,
        sha256="c" * 64,
    )
    db_session.add(f)
    db_session.commit()
    db_session.refresh(f)

    request = SimpleNamespace(headers={"if-none-match": f'"{f.sha256}"'})
    res = files_api.stl_response(f, request)
    assert res.status_code == 304
