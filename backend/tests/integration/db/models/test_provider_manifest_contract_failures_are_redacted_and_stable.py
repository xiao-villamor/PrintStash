"""Defends provider manifest contract failures are redacted and stable at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._provider_resolution_context_shared import (
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    ProviderModelMetadata,
    Session,
    User,
    _factory,
    asyncio,
    import_resolvers,
    json,
    pytest,
)


@pytest.mark.parametrize(
    ("provider", "url", "fetch_name"),
    [
        (
            "myminifactory",
            "https://www.myminifactory.com/object/contract-bad",
            "fetch_mmf_model_metadata",
        ),
        (
            "cults",
            "https://cults3d.com/en/3d-model/art/contract-bad",
            "fetch_cults_model_metadata",
        ),
    ],
)
def test_provider_manifest_contract_failures_are_redacted_and_stable(
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    url: str,
    fetch_name: str,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    malicious = "<script>bearer-secret</script>" + ("x" * 70_000)

    async def malformed(*_args, **_kwargs):
        return ProviderModelMetadata(
            model_id="contract-bad",
            title=malicious,
            description=None,
            creator=None,
            license_name=None,
        )

    monkeypatch.setattr(import_resolvers.provider_connections, fetch_name, malformed)
    with pytest.raises(import_resolvers.ImportError_) as exc_info:
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                url, import_resolvers.ProviderResolutionContext(1, _factory())
            )
        )
    assert str(exc_info.value) == "provider_contract_changed"
    assert "bearer-secret" not in str(exc_info.value)
    assert "bearer-secret" not in repr(import_resolvers._provider_metadata_cache)


@pytest.mark.parametrize(
    ("url", "fetch_name"),
    [
        (
            "https://www.myminifactory.com/object/provider-secret",
            "fetch_mmf_model_metadata",
        ),
        (
            "https://cults3d.com/en/3d-model/art/provider-secret",
            "fetch_cults_model_metadata",
        ),
    ],
)
def test_provider_adapter_unexpected_failure_does_not_cross_inbox_seam(
    monkeypatch: pytest.MonkeyPatch, url: str, fetch_name: str
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def failing(*_args, **_kwargs):
        raise ValueError("raw provider body bearer-secret")

    monkeypatch.setattr(import_resolvers.provider_connections, fetch_name, failing)
    with pytest.raises(import_resolvers.ImportError_) as exc_info:
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                url, import_resolvers.ProviderResolutionContext(1, _factory())
            )
        )
    assert str(exc_info.value) == "provider_contract_changed"
    assert "bearer-secret" not in str(exc_info.value)


def test_mmf_signed_url_is_transient_not_in_persisted_inbox_manifest(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(username="mmf-manifest", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    assert user.id is not None
    manifest = {
        "schema_version": 2,
        "kind": "model_files",
        "source": {
            "provider": "myminifactory",
            "canonical_url": "https://www.myminifactory.com/object/123",
            "source_item_id": "123",
            "source_revision": None,
            "adapter_version": "provider-api-v1",
            "fields": {"title": {"value": "Widget", "origin": "confirmed"}},
            "tags": [],
        },
        "files": [
            {"id": "file-1", "name": "widget.3mf", "file_type": "3mf", "size": 1}
        ],
        "selected_ids": ["file-1"],
    }
    row = InboxItem(
        owner_user_id=user.id,
        source_kind=InboxSourceKind.URL,
        source_url=manifest["source"]["canonical_url"],
        state=InboxItemState.REVIEW,
        manifest_json=json.dumps(manifest),
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    assert row.source_url is not None and row.id is not None
    signed = "https://download.example.test/file?token=never-persist"

    async def url(_session, _user_id, _file_id):
        return signed

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_file_download_url", url
    )
    assets = asyncio.run(
        import_resolvers.resolve_selected_assets(
            row.source_url,
            __import__(
                "printstash_core.imports", fromlist=["CaptureManifestV2"]
            ).CaptureManifestV2.from_dict(manifest),
            ["file-1"],
            import_resolvers.ProviderResolutionContext(user.id, _factory()),
        )
    )
    assert assets[0].download_url == signed
    db_session.expire_all()
    persisted = db_session.get(InboxItem, row.id)
    assert persisted is not None
    assert "never-persist" not in persisted.manifest_json
    assert "never-persist" not in repr(import_resolvers._provider_metadata_cache)
