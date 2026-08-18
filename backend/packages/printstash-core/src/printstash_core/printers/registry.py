"""An instance-owned registry for printer provider factories."""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast, overload

from .contracts import PrinterClient, ProviderFactory
from .models import PrinterConfig, ProviderCapabilities, ProviderError, ProviderId


class ProviderRegistry:
    """Pure registry with no application globals or persistence dependencies."""

    def __init__(self, factories: Iterable[ProviderFactory] = ()) -> None:
        self._factories: dict[ProviderId, ProviderFactory] = {}
        for factory in factories:
            self.register(factory)

    @overload
    def register(self, factory: ProviderFactory, /) -> ProviderFactory: ...

    @overload
    def register(
        self, provider_id: ProviderId | str, factory: ProviderFactory, /
    ) -> ProviderFactory: ...

    def register(
        self,
        provider_or_factory: ProviderId | str | ProviderFactory,
        factory: ProviderFactory | None = None,
        /,
    ) -> ProviderFactory:
        """Register a factory, either directly or under an explicit id."""

        if factory is None:
            candidate = cast(ProviderFactory, provider_or_factory)
            raw_provider_id = getattr(candidate, "provider_id", None)
            if raw_provider_id is None:
                raise ProviderError(
                    "invalid_provider_factory", code="invalid_provider_factory"
                )
        else:
            candidate = factory
            raw_provider_id = provider_or_factory

        provider_id = self._provider_id(raw_provider_id)
        if provider_id in self._factories:
            raise ProviderError(
                "provider_already_registered", code="provider_already_registered"
            )
        self._factories[provider_id] = candidate
        return candidate

    def build(
        self, provider_id: ProviderId | str, config: PrinterConfig
    ) -> PrinterClient:
        expected_provider = self._provider_id(provider_id)
        if config.provider_id is not expected_provider:
            raise ProviderError(
                "provider_config_mismatch", code="provider_config_mismatch"
            )
        return self._factory(expected_provider).build(config)

    def capabilities(self, provider_id: ProviderId | str) -> ProviderCapabilities:
        return self._factory(self._provider_id(provider_id)).capabilities

    @property
    def providers(self) -> tuple[ProviderId, ...]:
        return tuple(self._factories)

    def _factory(self, provider_id: ProviderId) -> ProviderFactory:
        try:
            return self._factories[provider_id]
        except KeyError:
            raise ProviderError("unknown_provider", code="unknown_provider") from None

    @staticmethod
    def _provider_id(value: object) -> ProviderId:
        try:
            return ProviderId(cast(str, value))
        except (TypeError, ValueError):
            raise ProviderError("unknown_provider", code="unknown_provider") from None
