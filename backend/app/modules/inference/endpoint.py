"""Closed immutable remote endpoint configuration; URLs never come from queries."""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from urllib.parse import unquote, urlsplit, urlunsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)


class EndpointParameters(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)
    base_url: str = Field(min_length=1, max_length=2048)
    model: str = Field(min_length=1, max_length=128)
    revision: str = Field(default="configured-v1", min_length=1, max_length=128)
    model_repo: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$",
    )
    api_key: SecretStr = Field(default=SecretStr(""), max_length=8192)
    headers: dict[str, SecretStr] = Field(default_factory=dict, max_length=16)
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    max_input_characters: int = Field(default=16384, ge=128, le=16384)
    prefer_responses: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        url = urlsplit(value)
        if (
            url.scheme not in {"http", "https"}
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or any(ord(char) < 33 for char in value)
            or "\\" in value
            or any(segment in {".", ".."} for segment in unquote(url.path).split("/"))
        ):
            raise ValueError("inference_endpoint_invalid")
        _ = url.port  # Reject invalid/out-of-range port syntax here, before HTTP.
        return urlunsplit(
            (url.scheme, url.netloc.lower(), url.path.rstrip("/"), "", "")
        )

    @model_validator(mode="after")
    def validate_headers(self):
        normalized = set()
        for name, secret in self.headers.items():
            value = secret.get_secret_value()
            if (
                not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", name)
                or name.lower() in normalized
                or name.lower()
                in {
                    "host",
                    "content-length",
                    "content-type",
                    "transfer-encoding",
                    "connection",
                    "accept-encoding",
                    "proxy-authorization",
                }
                or len(value) > 8192
                or any(char in value for char in "\r\n\0")
            ):
                raise ValueError("inference_header_invalid")
            normalized.add(name.lower())
        if self.api_key.get_secret_value() and "authorization" in normalized:
            raise ValueError("inference_authorization_ambiguous")
        if any(char in self.api_key.get_secret_value() for char in "\r\n\0"):
            raise ValueError("inference_header_invalid")
        return self

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    def request_headers(self) -> dict[str, str]:
        headers = {
            name: value.get_secret_value() for name, value in self.headers.items()
        }
        if self.api_key.get_secret_value():
            headers["Authorization"] = "Bearer " + self.api_key.get_secret_value()
        return headers | {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Accept-Encoding": "identity",
        }


class EndpointConfig(EndpointParameters):
    configuration_version: str = Field(
        default_factory=lambda: secrets.token_hex(16), pattern=r"^[0-9a-f]{32}$"
    )

    @property
    def identity(self) -> str:
        # Server-owned immutable version includes credential changes without
        # exposing credential hashes or depending on the installation JWT key.
        public = self.model_dump(exclude={"api_key", "headers"}, exclude_none=True)
        return hashlib.sha256(
            json.dumps(public, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
