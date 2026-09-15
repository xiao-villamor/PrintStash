"""Persist probed endpoint versions; callers cannot mutate an active identity."""

from __future__ import annotations

import json
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image
from printstash_core.inference import EmbeddingError, EmbeddingSpace
from printstash_core.inference.chat import ChatInput
from printstash_core.inference.model_capabilities import capabilities_for_identity
from pydantic import SecretStr
from sqlmodel import Session, select

from app.core.errors import ErrorKind, OperationError
from app.db.models import InferenceEndpoint
from app.modules.administration import audit
from app.modules.inference.chat import RemoteChatProvider
from app.modules.inference.endpoint import EndpointConfig
from app.modules.inference.local import LocalEmbeddingProvider
from app.modules.inference.remote import RemoteEmbeddingProvider
from app.schemas.inference import EndpointProposal, EndpointRead


def load(row: InferenceEndpoint) -> EndpointConfig:
    values = json.loads(row.config_json)
    values["api_key"] = SecretStr(row.api_key)
    values["headers"] = {
        key: SecretStr(value) for key, value in json.loads(row.headers_json).items()
    }
    config = EndpointConfig.model_validate(values)
    if config.identity != row.config_hash:
        raise EmbeddingError("inference_configuration_mismatch")
    return config


def read(row: InferenceEndpoint) -> EndpointRead:
    config = load(row)
    capabilities = capabilities_for_identity(
        config.model_repo, config.revision, row.native_dimension
    )
    return EndpointRead(
        id=row.id,
        kind=row.kind,
        host=config.host,
        base_url=config.base_url,
        model=config.model,
        revision=config.revision,
        model_repo=config.model_repo,
        mrl_dimensions=list(capabilities.mrl_dimensions) if capabilities else [],
        config_hash=row.config_hash,
        native_dimension=row.native_dimension,
        supports_images=row.supports_images,
        dialect=row.dialect,
        guarantee={
            "json_schema": "schema_constrained",
            "responses": "schema_constrained",
            "tools": "tool_constrained",
            "json": "validated_json",
        }.get(row.dialect),
        has_credentials=bool(config.api_key.get_secret_value() or config.headers),
        header_names=sorted(config.headers),
        timeout_seconds=config.timeout_seconds,
        max_input_characters=config.max_input_characters,
        prefer_responses=config.prefer_responses,
    )


def list_endpoints(session: Session) -> list[EndpointRead]:
    return [
        read(row)
        for row in session.exec(
            select(InferenceEndpoint).order_by(InferenceEndpoint.id).limit(64)
        ).all()
    ]


def create(session: Session, proposal: EndpointProposal) -> EndpointRead:
    # Bound stored endpoint versions as well as the live transport circuit map.
    if len(session.exec(select(InferenceEndpoint.id).limit(64)).all()) >= 64:
        raise OperationError("inference_endpoint_limit", kind=ErrorKind.CONFLICT)
    parameters = proposal.model_dump(
        exclude={
            "kind",
            "native_dimension",
            "supports_images",
            "inherit_credentials_from_id",
        }
    )
    if proposal.inherit_credentials_from_id is not None:
        source = session.get(InferenceEndpoint, proposal.inherit_credentials_from_id)
        if source is None or source.kind != proposal.kind:
            raise OperationError(
                "inference_endpoint_unavailable", kind=ErrorKind.INVALID
            )
        original = load(source)
        before, after = urlsplit(original.base_url), urlsplit(proposal.base_url)
        if (
            before.scheme,
            before.hostname,
            before.port or (443 if before.scheme == "https" else 80),
        ) != (
            after.scheme,
            after.hostname,
            after.port or (443 if after.scheme == "https" else 80),
        ):
            raise OperationError(
                "inference_credential_origin_changed", kind=ErrorKind.INVALID
            )
        if "api_key" not in proposal.model_fields_set:
            parameters["api_key"] = original.api_key
        if "headers" not in proposal.model_fields_set:
            parameters["headers"] = original.headers
    endpoint = EndpointConfig(**parameters)
    dialect = None
    try:
        if proposal.kind == "embedding":
            space = EmbeddingSpace(
                model_key=endpoint.model,
                model_revision=endpoint.revision,
                dimension=proposal.native_dimension,
                modality="text",
                render_recipe="capability-probe-v1",
                provider="openai_compatible",
                profile="capability_probe",
                provider_config_hash=endpoint.identity,
            )
            RemoteEmbeddingProvider(endpoint, space).validate()
        else:
            images = ()
            field, expected = "probe", "ok"
            instruction = 'Return {"probe":"ok"}.'
            if proposal.supports_images:
                image = BytesIO()
                Image.new("RGB", (32, 32), "red").save(image, format="JPEG")
                images = (image.getvalue(),)
                field, expected = "color", "red"
                instruction = "Identify the dominant color of the image."
            result = RemoteChatProvider(
                endpoint, supports_images=proposal.supports_images
            ).complete(
                ChatInput(
                    instruction=instruction,
                    text="Capability probe",
                    image_jpegs=images,
                    schema={
                        "type": "object",
                        "properties": {field: {"type": "string", "enum": [expected]}},
                        "required": [field],
                        "additionalProperties": False,
                    },
                    max_output_tokens=64,
                )
            )
            dialect = result.dialect
    except EmbeddingError as exc:
        raise OperationError(exc.code, kind=ErrorKind.INVALID) from None
    row = InferenceEndpoint(
        config_hash=endpoint.identity,
        kind=proposal.kind,
        config_json=endpoint.model_dump_json(exclude={"api_key", "headers"}),
        api_key=endpoint.api_key.get_secret_value(),
        headers_json=json.dumps(
            {key: value.get_secret_value() for key, value in endpoint.headers.items()}
        ),
        native_dimension=proposal.native_dimension,
        supports_images=proposal.supports_images,
        dialect=dialect,
    )
    session.add(row)
    session.flush()
    audit.record(
        session,
        action="inference_endpoint_created",
        resource_type="inference_endpoint",
        resource_id=row.id,
        diff={"kind": row.kind, "host": endpoint.host, "model": endpoint.model},
    )
    return read(row)


def embedding_provider(
    session: Session, space: EmbeddingSpace
) -> RemoteEmbeddingProvider | LocalEmbeddingProvider:
    if space.provider == "onnx_cpu":
        from app.core.config import settings
        from app.db.session import get_session_factory
        from app.modules.inference.model_cache import for_space
        from app.modules.search.settings import settings as search_settings

        if not search_settings(session).local_models_enabled:
            raise EmbeddingError("embedding_local_disabled")

        model = for_space(space)
        return LocalEmbeddingProvider(
            get_session_factory(),
            model.directory,
            model.manifest.model_key,
            settings.embedding_onnx_threads,
            space=space,
        )
    row = session.exec(
        select(InferenceEndpoint).where(
            InferenceEndpoint.config_hash == space.provider_config_hash,
            InferenceEndpoint.kind == "embedding",
        )
    ).first()
    if row is None:
        raise EmbeddingError("inference_endpoint_unavailable")
    return RemoteEmbeddingProvider(load(row), space)


def chat_provider(session: Session, endpoint_id: int) -> RemoteChatProvider:
    row = session.get(InferenceEndpoint, endpoint_id)
    if row is None or row.kind != "chat":
        raise EmbeddingError("inference_chat_unavailable")
    return RemoteChatProvider(
        load(row), supports_images=row.supports_images, dialect=row.dialect
    )
