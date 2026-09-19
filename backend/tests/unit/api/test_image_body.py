"""The HTTP image parser bounds raw and multipart bodies without disk spooling."""

import pytest
from printstash_core.inference.images import MAX_IMAGE_BYTES

from app.api.image_body import MAX_BODY, read_image
from app.core.errors import OperationError

pytestmark = pytest.mark.asyncio


async def chunks(payload, size=17):
    for i in range(0, len(payload), size):
        yield payload[i : i + size]


def multipart(*, name="image", extra=b"", closed=True):
    return (
        b'--test\r\nContent-Disposition: form-data; name="'
        + name.encode()
        + b'"; filename="private.png"\r\nContent-Type: image/png\r\n\r\nimage bytes\r\n'
        + extra
        + (b"--test--\r\n" if closed else b"")
    )


class TestImageBody:
    async def test_reads_chunked_multipart_in_memory(self):
        assert await read_image(
            chunks(multipart(), 1), "multipart/form-data; boundary=test", None
        ) == (b"image bytes", "image/png")

    async def test_reads_raw_image_bytes(self):
        assert await read_image(chunks(b"image bytes"), "image/webp", None) == (
            b"image bytes",
            "image/webp",
        )

    @pytest.mark.parametrize("length", [str(MAX_BODY + 1), "-1", "invalid"])
    async def test_rejects_invalid_declared_size_before_consuming_body(self, length):
        async def unread():
            raise AssertionError("oversized body must not be read")
            yield b""

        with pytest.raises(OperationError):
            await read_image(unread(), "image/png", length)

    async def test_counts_bytes_instead_of_trusting_declared_size(self):
        with pytest.raises(OperationError, match="embedding_image_too_large"):
            await read_image(
                chunks(b"x" * (MAX_IMAGE_BYTES + 1), 65536), "image/png", "1"
            )

    @pytest.mark.parametrize(
        "payload",
        [
            multipart(name="other"),
            multipart(closed=False),
            multipart(extra=multipart()),
            multipart().replace(
                b"Content-Type: image/png",
                b"Content-Type: image/png\r\nContent-Type: image/jpeg",
            ),
            multipart().replace(b"private.png", b"p" * 5000),
        ],
        ids=[
            "wrong-field",
            "truncated",
            "extra-part",
            "duplicate-header",
            "large-header",
        ],
    )
    async def test_rejects_invalid_multipart_bodies(self, payload):
        with pytest.raises(OperationError, match="embedding_image_invalid"):
            await read_image(
                chunks(payload), "multipart/form-data; boundary=test", None
            )
