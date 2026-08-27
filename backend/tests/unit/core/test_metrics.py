"""Defends ``test_capture_metrics_allowlist_malicious_labels`` behavior for the ``core`` production unit.

A failure means this boundary no longer preserves its observable contract.
"""

from __future__ import annotations

import asyncio
from typing import cast

from prometheus_client import generate_latest

from app.core.metrics import record_capture_operation, registry
from app.db.session import SessionFactory
from app.services import import_resolvers
from app.services.capture_provider_connections import (
    ProviderIdentity,
    ProviderModelMetadata,
)


def test_capture_metrics_allowlist_malicious_labels() -> None:
    hostile = "https://evil.test/a?token=secret-user-model-file"
    record_capture_operation(
        hostile,
        hostile,
        hostile,
        0.1,
        uploaded_bytes=7,
        error_category=hostile,
    )
    text = generate_latest(registry).decode()
    assert 'provider="unknown"' in text
    assert hostile not in text
    assert "secret-user-model-file" not in text


def test_provider_resolver_emits_one_bounded_success_observation(monkeypatch) -> None:
    import_resolvers._provider_metadata_cache.clear()

    class Factory:
        def scoped_session(self):
            class S:
                def __enter__(self):
                    return object()

                def __exit__(self, *_args):
                    return False

            return S()

    async def metadata(*_args):
        return ProviderModelMetadata(
            "1",
            "Widget",
            None,
            None,
            None,
            (),
            identity=ProviderIdentity(
                provider_id="1",
                canonical_url="https://www.myminifactory.com/object/1",
            ),
        )

    monkeypatch.setattr(
        import_resolvers.provider_connections, "fetch_mmf_model_metadata", metadata
    )
    asyncio.run(
        import_resolvers.resolve_connected_provider_capture(
            "https://www.myminifactory.com/object/1",
            import_resolvers.ProviderResolutionContext(
                1, cast(SessionFactory, Factory())
            ),
        )
    )
    text = generate_latest(registry).decode()
    assert 'outcome="success",provider="myminifactory",transport="provider_api"' in text


def test_upload_transports_and_manual_file_error_are_bounded() -> None:
    record_capture_operation(
        "makerworld", "browser_upload", "success", 0, uploaded_bytes=3
    )
    record_capture_operation(
        "makerworld", "upload_slots", "success", 0, uploaded_bytes=4
    )
    record_capture_operation(
        "cults",
        "provider_api",
        "required",
        0,
        error_category="user_file_required",
    )
    text = generate_latest(registry).decode()
    assert 'provider="makerworld"' in text
    assert 'transport="browser_upload"' in text
    assert 'transport="upload_slots"' in text
    assert 'category="user_file_required",provider="cults"' in text
