"""Resolving a pasted URL into something importable, with a cache that is per-user.

A provider lookup costs a network round trip and a rate-limit slot, so results are
cached. That cache is the interesting part, because it holds **credentialed**
metadata: a response fetched with one user's token must never be served to
another. So it is owner-scoped, and this file asserts that directly rather than
trusting the key format.

The invalidation rows follow from the same concern. Connecting or disconnecting a
provider changes what that user is allowed to see, so both drop their cache; a
disconnect that left a warm entry would keep serving data the user no longer has
access to. Expiry is asserted too — a stale entry is a lookup that silently stops
reflecting the source.

The refusal rows are about failing usefully. "You need to connect this provider"
is a stable, actionable code rather than a generic error, because it is the one
failure the user can fix themselves. And the manifest built from provider metadata
is bounded and omits fields it does not recognise, since an unknown field
forwarded verbatim is untrusted data reaching the import path.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from typing import cast

import pytest
from sqlmodel import Session

from app.core.time import utcnow
from app.db.models import (
    CaptureProvider,
    InboxItem,
    InboxItemState,
    InboxSourceKind,
    ProviderConnection,
)
from app.db.session import SessionFactory, SQLiteSessionFactory
from app.services import import_resolvers, provider_connections
from app.services.capture_provider_connections import (
    ProviderConnectionError,
    ProviderFileMetadata,
    ProviderIdentity,
    ProviderModelMetadata,
)
from tests.factories import build_user


class _Factory:
    def scoped_session(self):
        class _Session:
            def __enter__(self):
                return object()

            def __exit__(self, *_args):
                return False

        return _Session()


def _factory() -> SessionFactory:
    return cast(SessionFactory, _Factory())


def _seed_stale_cache(user_id: int) -> tuple[int, str, str]:
    """Put a live cache entry in front of *user_id*, and return its key.

    Deliberately not expired: an entry that would have lapsed on its own proves
    nothing about invalidation.
    """
    import_resolvers._provider_metadata_cache.clear()
    key = (user_id, "cults", "widget")
    import_resolvers._provider_metadata_cache[key] = (
        ProviderModelMetadata("widget", "stale", None, None, None),
        utcnow() + timedelta(minutes=5),
    )
    return key


class TestProviderMetadataCache:
    """Caching a provider's answer per owner, and re-checking when the connection changes."""

    def test_provider_metadata_cache_is_owner_scoped(
        self,
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

    def test_expired_provider_metadata_cache_fetches_again(
        self,
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

    def test_provider_metadata_cache_rechecks_connection_after_disconnect(
        self,
        db_session: Session,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import_resolvers._provider_metadata_cache.clear()
        user = build_user(db_session, "cache-owner")
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
            import_resolvers.provider_connections,
            "fetch_cults_model_metadata",
            metadata,
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
            asyncio.run(
                import_resolvers.resolve_connected_provider_capture(url, context)
            )
        assert calls == 2
        assert not import_resolvers._provider_metadata_cache

    def test_connecting_a_provider_drops_that_owners_cached_metadata(
        self, db_session: Session
    ) -> None:
        user = build_user(db_session, "cache-on-connect")
        assert user.id is not None
        key = _seed_stale_cache(user.id)

        provider_connections.connect_cults(db_session, user.id, "user", "password")

        assert key not in import_resolvers._provider_metadata_cache

    def test_disconnecting_a_provider_drops_that_owners_cached_metadata(
        self, db_session: Session
    ) -> None:
        # Both halves matter and neither implies the other: a stale entry surviving
        # a *connect* serves the previous account's titles, and one surviving a
        # *disconnect* serves them after the credential is gone.
        user = build_user(db_session, "cache-on-disconnect")
        assert user.id is not None
        provider_connections.connect_cults(db_session, user.id, "user", "password")
        key = _seed_stale_cache(user.id)

        assert provider_connections.disconnect_provider_connection(
            db_session, user.id, CaptureProvider.CULTS
        )

        assert key not in import_resolvers._provider_metadata_cache

    def test_provider_connection_required_is_stable(
        self,
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

    def test_cults_disconnected_requires_provider_connection(
        self,
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

    def test_public_capture_resolver_defers_cults_to_connection_context(self) -> None:
        url = "https://cults3d.com/en/3d-model/art/widget"

        assert asyncio.run(import_resolvers.resolve_capture_manifest(url)) is None


class TestConnectedManifest:
    """Turning an authenticated provider's response into a bounded capture manifest."""

    def test_mmf_manifest_carries_only_allowlisted_metadata(
        self,
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

    def test_cults_manifest_carries_only_allowlisted_metadata(
        self,
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
        object.__setattr__(
            metadata, "temporary_download_url", "https://cdn.example/temp"
        )
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
        self,
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
        self,
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
        self,
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

    def test_mmf_signed_url_is_transient_not_in_persisted_inbox_manifest(
        self, db_session: Session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        user = build_user(db_session, "mmf-manifest")
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


class TestProviderFailures:
    """A provider that misbehaves must not leak its detail or its exception across the seam."""

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
    def test_a_provider_error_surfaces_once_without_a_retry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        error: ProviderConnectionError,
        expected: str,
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
    def test_a_malformed_manifest_fails_without_echoing_its_content(
        self,
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

        monkeypatch.setattr(
            import_resolvers.provider_connections, fetch_name, malformed
        )
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
        self, monkeypatch: pytest.MonkeyPatch, url: str, fetch_name: str
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


class TestUser:
    def test_cults_connected_manifest_requires_user_file_at_import(
        self,
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
            import_resolvers.provider_connections,
            "fetch_cults_model_metadata",
            metadata,
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
