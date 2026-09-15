"""Bounded raw/multipart image parsing without UploadFile or temporary files."""

from __future__ import annotations

from printstash_core.inference.images import MAX_IMAGE_BYTES
from python_multipart.multipart import MultipartParser, parse_options_header

from app.core.errors import ErrorKind, OperationError

MAX_BODY = MAX_IMAGE_BYTES + 16 * 1024


async def read_image(stream, content_type: str, content_length: str | None):
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError:
            raise OperationError("embedding_image_invalid") from None
        if not 0 <= size <= MAX_BODY:
            raise OperationError("embedding_image_too_large", kind=ErrorKind.TOO_LARGE)
    mime, options = parse_options_header(content_type)
    payload = bytearray()
    total = 0
    if mime != b"multipart/form-data":
        async for chunk in stream:
            total += len(chunk)
            if total > MAX_IMAGE_BYTES:
                raise OperationError(
                    "embedding_image_too_large", kind=ErrorKind.TOO_LARGE
                )
            payload.extend(chunk)
        return bytes(payload), content_type
    boundary = options.get(b"boundary", b"")
    if not 1 <= len(boundary) <= 70:
        raise OperationError("embedding_image_invalid")
    field, value = bytearray(), bytearray()
    headers = {}
    parts = 0
    complete = False

    def part_begin():
        nonlocal parts
        parts += 1
        if parts > 1:
            raise OperationError("embedding_image_invalid")

    def header_field(data, start, end):
        field.extend(data[start:end])

    def header_value(data, start, end):
        value.extend(data[start:end])

    def header_end():
        key = bytes(field).lower()
        if key in headers:
            raise OperationError("embedding_image_invalid")
        headers[key] = bytes(value)
        field.clear()
        value.clear()

    def headers_finished():
        disposition, attributes = parse_options_header(
            headers.get(b"content-disposition", b"")
        )
        if (
            disposition != b"form-data"
            or attributes.get(b"name") != b"image"
            or b"filename" not in attributes
        ):
            raise OperationError("embedding_image_invalid")

    def part_data(data, start, end):
        if len(payload) + end - start > MAX_IMAGE_BYTES:
            raise OperationError("embedding_image_too_large", kind=ErrorKind.TOO_LARGE)
        payload.extend(data[start:end])

    def end():
        nonlocal complete
        complete = True

    parser = MultipartParser(
        boundary,
        {
            "on_part_begin": part_begin,
            "on_header_field": header_field,
            "on_header_value": header_value,
            "on_header_end": header_end,
            "on_headers_finished": headers_finished,
            "on_part_data": part_data,
            "on_end": end,
        },
        max_size=MAX_BODY,
        max_header_count=4,
        max_header_size=4096,
    )
    try:
        async for chunk in stream:
            total += len(chunk)
            if total > MAX_BODY:
                raise OperationError(
                    "embedding_image_too_large", kind=ErrorKind.TOO_LARGE
                )
            parser.write(chunk)
        parser.finalize()
        if not complete or parts != 1:
            raise OperationError("embedding_image_invalid")
        return bytes(payload), headers.get(b"content-type", b"").decode("ascii")
    except (ValueError, UnicodeError):
        raise OperationError("embedding_image_invalid") from None
