"""Real S3 delivery fixture with test-owned TLS and bucket administration."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import boto3

from app.core.config import _overlay
from app.modules.storage.storage_backend.s3 import S3StorageBackend
from tests.containers import S3_ACCESS_KEY, S3_SECRET_KEY, s3_endpoint
from tests.fakes.tls_storage import tls_storage


@contextmanager
def browser_s3(directory: Path, *, origin: str | None = "https://app.test"):
    with tls_storage(s3_endpoint(), directory) as tls:
        bucket = f"delivery-{uuid.uuid4().hex[:12]}"
        client = boto3.client(
            "s3",
            endpoint_url=tls.endpoint,
            region_name="us-east-1",
            aws_access_key_id=S3_ACCESS_KEY,
            aws_secret_access_key=S3_SECRET_KEY,
            verify=str(tls.ca_file),
        )
        client.create_bucket(Bucket=bucket)
        if origin is not None:
            client.put_bucket_cors(
                Bucket=bucket,
                CORSConfiguration={
                    "CORSRules": [
                        {
                            "AllowedOrigins": [origin],
                            "AllowedMethods": ["GET", "HEAD"],
                            "AllowedHeaders": ["*"],
                            "ExposeHeaders": ["Content-Disposition"],
                        }
                    ]
                },
            )
        options = {
            "s3_bucket": bucket,
            "s3_endpoint_url": tls.endpoint,
            "s3_region": "us-east-1",
            "s3_access_key": S3_ACCESS_KEY,
            "s3_secret_key": S3_SECRET_KEY,
            "s3_addressing_style": "path",
        }
        missing = object()
        old_options = {key: _overlay.get(key, missing) for key in options}
        old_ca = os.environ.get("AWS_CA_BUNDLE")
        _overlay.update(options)
        os.environ["AWS_CA_BUNDLE"] = str(tls.ca_file)
        try:
            yield S3StorageBackend(), tls
        finally:
            if old_ca is None:
                os.environ.pop("AWS_CA_BUNDLE", None)
            else:
                os.environ["AWS_CA_BUNDLE"] = old_ca
            for key, value in old_options.items():
                if value is missing:
                    _overlay.pop(key, None)
                else:
                    _overlay[key] = value
