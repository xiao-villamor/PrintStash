"""Defends url safety at the models API integration boundary.

A regression could expose the wrong artifact or corrupt revision and thumbnail state.
"""

from __future__ import annotations

from ._cross_unit_shared import (
    pytest,
)


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/x.stl",
        "http://127.0.0.1/x.stl",
        "http://localhost/x.stl",
        "http://10.0.0.5/x.stl",
        "http://192.168.1.10/x.stl",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/x.stl",
    ],
)
def test_validate_public_url_rejects_unsafe(url):
    from app.services import importer

    with pytest.raises(importer.ImportError_):
        importer.validate_public_url(url)


def test_validate_public_url_accepts_public_host():
    from app.services import importer

    # Public DNS name should validate (resolves to public IPs).
    importer.validate_public_url("https://example.com/model.stl")
