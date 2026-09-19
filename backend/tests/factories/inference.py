"""Immutable test endpoint versions with encrypted, obviously fake credentials."""

import json
from typing import Any

from sqlmodel import Session

from app.db.models import InferenceEndpoint
from app.modules.inference.endpoint import EndpointConfig
from tests.factories._support import save


def build_inference_endpoint(
    session: Session, *, kind: str = "embedding", **overrides: Any
) -> InferenceEndpoint:
    config = overrides.pop("config", None) or EndpointConfig(
        base_url="http://inference.test/v1",
        model="test-embedding",
        api_key="test-factory-key",
    )
    values = {
        "config_hash": config.identity,
        "kind": kind,
        "config_json": config.model_dump_json(exclude={"api_key", "headers"}),
        "api_key": config.api_key.get_secret_value(),
        "headers_json": json.dumps(
            {key: value.get_secret_value() for key, value in config.headers.items()}
        ),
        "native_dimension": 4 if kind == "embedding" else None,
        "dialect": "json_schema" if kind == "chat" else None,
    }
    return save(session, InferenceEndpoint(**(values | overrides)))
