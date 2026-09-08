"""Delivery telemetry records consumption without retaining bearer capabilities."""

from prometheus_client import generate_latest

from app.core.metrics import registry
from app.modules.storage import delivery_observability as telemetry


def test_counts_consumed_proxy_bytes():
    labels = {"provider": "s3", "purpose": "download"}
    before = (
        registry.get_sample_value("printstash_delivery_proxied_bytes_total", labels)
        or 0
    )
    stream = telemetry.MeteredChunks(iter([b"first", b"second"]), "s3", "download")

    assert next(stream) == b"first"
    assert (
        registry.get_sample_value("printstash_delivery_proxied_bytes_total", labels)
        == before + 5
    )
    stream.close()


def test_releases_metered_stream_on_disconnect():
    released = []

    def source():
        try:
            yield b"first"
            yield b"second"
        finally:
            released.append(True)

    stream = telemetry.MeteredChunks(source(), "s3", "download")
    next(stream)

    stream.close()
    stream.close()

    assert released == [True]


def test_redacts_unknown_delivery_labels():
    secret = "https://private.test/object?signature=secret-artifact"
    telemetry.record_strategy(secret, secret, secret)
    stream = telemetry.MeteredChunks(iter([b"payload"]), secret, secret)
    list(stream)

    exposition = generate_latest(registry).decode()
    assert secret not in exposition
    assert "secret-artifact" not in exposition
    assert 'provider="unknown",purpose="unknown",strategy="unknown"' in exposition


def test_closed_proxy_stream_stays_closed():
    stream = telemetry.MeteredChunks(iter([b"unused"]), "s3", "download")
    stream.close()

    assert list(stream) == []


def test_propagates_provider_stream_failure():
    import pytest

    def broken():
        raise OSError("provider unavailable")
        yield b""

    stream = telemetry.MeteredChunks(broken(), "s3", "download")

    with pytest.raises(OSError, match="provider unavailable"):
        next(stream)
    assert list(stream) == []


def test_keeps_proxy_bytes_when_metrics_fail(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(telemetry._bytes, "labels", fail)
    stream = telemetry.MeteredChunks(iter([b"payload"]), "s3", "download")

    assert list(stream) == [b"payload"]


def test_keeps_delivery_when_strategy_metrics_fail(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr(telemetry._strategies, "labels", fail)

    assert telemetry.record_strategy("s3", "download", "proxy") is None
