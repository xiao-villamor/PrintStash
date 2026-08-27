"""Defends provider connection required is stable at the db models integration boundary.

A regression could commit partial, unauthenticated, or internally inconsistent database state.
"""

from __future__ import annotations

from ._provider_resolution_context_shared import (
    CaptureProvider,
    ProviderConnection,
    ProviderConnectionError,
    ProviderFileMetadata,
    ProviderIdentity,
    ProviderModelMetadata,
    Session,
    SQLiteSessionFactory,
    User,
    _factory,
    asyncio,
    import_resolvers,
    provider_connections,
    pytest,
    timedelta,
    utcnow,
)


def test_provider_connection_required_is_stable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def missing(*_args, **_kwargs):
        raise ProviderConnectionError("provider_not_connected")

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_model_metadata", missing
    )
    with pytest.raises(
        import_resolvers.ImportError_, match="provider_connection_required"
    ):
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                "https://www.myminifactory.com/object/123",
                import_resolvers.ProviderResolutionContext(1, _factory()),
            )
        )


def test_provider_metadata_cache_is_owner_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    calls: list[int] = []

    async def metadata(_session, user_id: int, item: str):
        calls.append(user_id)
        return ProviderModelMetadata(
            item,
            "Widget",
            None,
            None,
            None,
            (),
            identity=ProviderIdentity(
                provider_id=item,
                canonical_url=f"https://www.myminifactory.com/object/{item}",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_model_metadata", metadata
    )
    url = "https://www.myminifactory.com/object/123"
    asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            url, import_resolvers.ProviderResolutionContext(1, _factory())
        )
    )
    asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            url, import_resolvers.ProviderResolutionContext(1, _factory())
        )
    )
    asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            url, import_resolvers.ProviderResolutionContext(2, _factory())
        )
    )
    assert calls == [1, 2]


def test_provider_metadata_cache_rechecks_connection_after_disconnect(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    user = User(username="cache-owner", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    assert user.id is not None
    connection = ProviderConnection(
        user_id=user.id,
        provider=CaptureProvider.CULTS,
        credential_secret="user\npassword",
    )
    db_session.add(connection)
    db_session.commit()
    calls = 0

    async def metadata(_session, _user_id: int, item: str):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ProviderConnectionError("provider_not_connected")
        return ProviderModelMetadata(
            item,
            "Widget",
            None,
            None,
            None,
            (),
            identity=ProviderIdentity(
                provider_id=item,
                canonical_slug=item,
                canonical_url=f"https://cults3d.com/en/3d-model/art/{item}",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", metadata
    )
    factory = SQLiteSessionFactory(db_session.get_bind())
    context = import_resolvers.ProviderResolutionContext(user.id, factory)
    url = "https://cults3d.com/en/3d-model/art/widget"

    assert asyncio.run(
        import_resolvers.resolve_connected_provider_capture(url, context)
    )
    db_session.delete(connection)
    db_session.commit()

    with pytest.raises(
        import_resolvers.ImportError_, match="provider_connection_required"
    ):
        asyncio.run(import_resolvers.resolve_connected_provider_capture(url, context))
    assert calls == 2
    assert not import_resolvers._provider_metadata_cache


def test_expired_provider_metadata_cache_fetches_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    calls: list[int] = []

    async def metadata(_session, _user_id: int, item: str):
        calls.append(1)
        return ProviderModelMetadata(
            item,
            "Widget",
            None,
            None,
            None,
            (),
            identity=ProviderIdentity(
                provider_id=item,
                canonical_url=f"https://www.myminifactory.com/object/{item}",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_model_metadata", metadata
    )
    import_resolvers._provider_metadata_cache[(1, "myminifactory", "123")] = (
        ProviderModelMetadata(
            "123",
            "stale",
            None,
            None,
            None,
            identity=ProviderIdentity(
                provider_id="123",
                canonical_url="https://www.myminifactory.com/object/123",
            ),
        ),
        utcnow() - timedelta(seconds=1),
    )

    manifest = asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            "https://www.myminifactory.com/object/123",
            import_resolvers.ProviderResolutionContext(1, _factory()),
        )
    )

    assert manifest is not None
    assert calls == [1]


def test_provider_connect_and_disconnect_invalidate_owner_cache(
    db_session: Session,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    user = User(username="cache-lifecycle", hashed_password="x")
    db_session.add(user)
    db_session.flush()
    assert user.id is not None
    key = (user.id, "cults", "widget")
    import_resolvers._provider_metadata_cache[key] = (
        ProviderModelMetadata("widget", "stale", None, None, None),
        utcnow() + timedelta(minutes=5),
    )

    provider_connections.connect_cults(db_session, user.id, "user", "password")
    assert key not in import_resolvers._provider_metadata_cache
    import_resolvers._provider_metadata_cache[key] = (
        ProviderModelMetadata("widget", "stale", None, None, None),
        utcnow() + timedelta(minutes=5),
    )

    assert provider_connections.disconnect_provider_connection(
        db_session, user.id, CaptureProvider.CULTS
    )
    assert key not in import_resolvers._provider_metadata_cache


def test_mmf_connected_manifest_maps_bounded_metadata_and_omits_unknown_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    metadata = ProviderModelMetadata(
        model_id="123",
        title="MMF widget",
        description="A printable widget",
        creator="Ada Maker",
        license_name="Creative Commons Attribution",
        files=(ProviderFileMetadata("file-1", "widget.3mf", 42),),
        tags=("functional", "calibration"),
        identity=ProviderIdentity(
            provider_id="123",
            canonical_url="https://www.myminifactory.com/object/123",
        ),
    )
    # ProviderModelMetadata has no URL, credential, or header fields.  Keep
    # this allowlist regression explicit if a provider response is widened.
    object.__setattr__(
        metadata, "download_url", "https://cdn.example/signed?token=secret"
    )
    object.__setattr__(metadata, "access_token", "bearer-secret")
    object.__setattr__(metadata, "headers", {"Authorization": "Bearer secret"})

    async def fetch(_session, _user_id: int, _item: str):
        return metadata

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_model_metadata", fetch
    )
    manifest = asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            "https://www.myminifactory.com/object/123",
            import_resolvers.ProviderResolutionContext(1, _factory()),
        )
    )

    assert manifest is not None
    assert manifest.source.to_dict()["fields"] == {
        "title": {"value": "MMF widget", "origin": "confirmed"},
        "description": {"value": "A printable widget", "origin": "confirmed"},
        "creator_name": {"value": "Ada Maker", "origin": "confirmed"},
        "license_text": {
            "value": "Creative Commons Attribution",
            "origin": "confirmed",
        },
    }
    assert manifest.source.tags == ("functional", "calibration")
    serialized = manifest.to_dict()
    assert "signed?token=secret" not in repr(serialized)
    assert "bearer-secret" not in repr(serialized)
    assert "Authorization" not in repr(serialized)


def test_cults_connected_manifest_maps_bounded_metadata_and_omits_unknown_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    metadata = ProviderModelMetadata(
        model_id="widget-slug",
        title="Cults widget",
        description="A Cults model",
        creator="Maker One",
        license_name="CC BY",
        tags=("decor",),
        identity=ProviderIdentity(
            provider_id="widget-slug",
            canonical_slug="widget-slug",
            canonical_url="https://cults3d.com/en/3d-model/art/widget-slug",
        ),
    )
    object.__setattr__(metadata, "temporary_download_url", "https://cdn.example/temp")
    object.__setattr__(metadata, "password", "cults-secret")

    async def fetch(_session, _user_id: int, _item: str):
        return metadata

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", fetch
    )
    manifest = asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            "https://cults3d.com/en/3d-model/art/widget-slug",
            import_resolvers.ProviderResolutionContext(1, _factory()),
        )
    )

    assert manifest is not None
    assert manifest.source.to_dict()["fields"] == {
        "title": {"value": "Cults widget", "origin": "confirmed"},
        "description": {"value": "A Cults model", "origin": "confirmed"},
        "creator_name": {"value": "Maker One", "origin": "confirmed"},
        "license_text": {"value": "CC BY", "origin": "confirmed"},
    }
    assert manifest.source.tags == ("decor",)
    serialized = manifest.to_dict()
    assert "cdn.example/temp" not in repr(serialized)
    assert "cults-secret" not in repr(serialized)


def test_cults_manifest_accepts_opaque_id_that_differs_from_url_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def fetch(_session, _user_id: int, _item: str):
        return ProviderModelMetadata(
            model_id="design-1",
            title="Fixture design",
            description=None,
            creator=None,
            license_name=None,
            identity=ProviderIdentity(
                provider_id="design-1",
                canonical_slug="fixture",
                canonical_url="https://cults3d.com/en/3d-model/art/fixture",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", fetch
    )
    manifest = asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            "https://cults3d.com/en/3d-model/art/fixture",
            import_resolvers.ProviderResolutionContext(1, _factory()),
        )
    )

    assert manifest is not None
    assert manifest.source.source_item_id == "design-1"


def test_cults_manifest_rejects_missing_canonical_evidence_even_when_ids_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def fetch(_session, _user_id: int, _item: str):
        # Even a matching slug is insufficient without the canonical URL
        # evidence returned by the Cults adapter.
        return ProviderModelMetadata(
            model_id="fixture",
            title="Fixture design",
            description=None,
            creator=None,
            license_name=None,
            identity=ProviderIdentity(
                provider_id="fixture",
                canonical_slug="fixture",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", fetch
    )
    with pytest.raises(
        import_resolvers.ImportError_, match="provider_contract_changed"
    ):
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                "https://cults3d.com/en/3d-model/art/fixture",
                import_resolvers.ProviderResolutionContext(1, _factory()),
            )
        )


def test_cults_manifest_rejects_opaque_id_substitution_when_canonical_slug_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def fetch(_session, _user_id: int, _item: str):
        return ProviderModelMetadata(
            model_id="other-id",
            title="Other design",
            description=None,
            creator=None,
            license_name=None,
            identity=ProviderIdentity(
                provider_id="other-id",
                canonical_slug="other",
                canonical_url="https://cults3d.com/en/3d-model/art/other",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", fetch
    )
    with pytest.raises(
        import_resolvers.ImportError_, match="provider_contract_changed"
    ):
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                "https://cults3d.com/en/3d-model/art/fixture",
                import_resolvers.ProviderResolutionContext(1, _factory()),
            )
        )


def test_cults_connected_manifest_requires_user_file_at_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def metadata(_session, _user_id: int, item: str):
        return ProviderModelMetadata(
            item,
            "Cults widget",
            None,
            None,
            None,
            (),
            identity=ProviderIdentity(
                provider_id=item,
                canonical_slug=item,
                canonical_url=f"https://cults3d.com/en/3d-model/art/{item}",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", metadata
    )
    url = "https://cults3d.com/en/3d-model/art/widget"
    context = import_resolvers.ProviderResolutionContext(1, _factory())
    manifest = asyncio.run(
        import_resolvers.resolve_connected_provider_capture(url, context)
    )
    assert manifest is not None
    with pytest.raises(import_resolvers.ImportError_, match="user_file_required"):
        asyncio.run(
            import_resolvers.resolve_selected_assets(url, manifest, [], context)
        )


def test_public_capture_resolver_defers_cults_to_connection_context() -> None:
    url = "https://cults3d.com/en/3d-model/art/widget"

    assert asyncio.run(import_resolvers.resolve_capture_manifest(url)) is None


def test_cults_disconnected_requires_provider_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import_resolvers._provider_metadata_cache.clear()

    async def missing(*_args, **_kwargs):
        raise ProviderConnectionError("provider_not_connected")

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_cults_model_metadata", missing
    )
    with pytest.raises(
        import_resolvers.ImportError_, match="provider_connection_required"
    ):
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                "https://cults3d.com/en/3d-model/art/widget",
                import_resolvers.ProviderResolutionContext(1, _factory()),
            )
        )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ProviderConnectionError("provider_retry_exhausted", retryable=True),
            "provider_rate_limited",
        ),
        (
            ProviderConnectionError("provider_response_invalid"),
            "provider_contract_changed",
        ),
    ],
)
def test_provider_errors_are_stable_and_single_call(
    monkeypatch: pytest.MonkeyPatch, error: ProviderConnectionError, expected: str
) -> None:
    import_resolvers._provider_metadata_cache.clear()
    calls = 0

    async def failing(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise error

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_model_metadata", failing
    )
    with pytest.raises(import_resolvers.ImportError_, match=expected):
        asyncio.run(
            import_resolvers.resolve_connected_provider_capture(
                "https://www.myminifactory.com/object/123",
                import_resolvers.ProviderResolutionContext(9, _factory()),
            )
        )
    assert calls == 1
