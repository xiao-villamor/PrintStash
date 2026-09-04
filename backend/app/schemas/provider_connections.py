from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ProviderConnectionRead(BaseModel):
    provider: Literal["myminifactory", "cults"]
    connected: bool
    updated_at: datetime | None = None


class OAuthAuthorizeRead(BaseModel):
    authorization_url: str


class CultsConnectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class BrowserPairingCreateRead(BaseModel):
    code: str
    expires_at: datetime


class BrowserPairingClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=8, max_length=256)
    name: str = Field(min_length=1, max_length=128)


class BrowserDevicePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)


class BrowserDeviceRead(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class BrowserPairingClaimRead(BaseModel):
    credential: str
    device: BrowserDeviceRead
