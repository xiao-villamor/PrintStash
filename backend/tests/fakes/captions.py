"""A deterministic VLM port; production tests still use real caption persistence."""

from io import BytesIO

from PIL import Image
from printstash_core.inference import EmbeddingError
from printstash_core.inference.chat import ChatResult


class CaptionProvider:
    def __init__(self, value=None, before_reply=None, error_code=None):
        self.value = (
            {"caption": "A flanged mounting bracket"} if value is None else value
        )
        self.before_reply = before_reply
        self.error_code = error_code
        self.requests = []

    def complete(self, request, *, context=None):
        self.requests.append(request)
        if self.before_reply:
            callback, self.before_reply = self.before_reply, None
            callback()
        if self.error_code:
            raise EmbeddingError(self.error_code)
        return ChatResult(self.value, "json_schema", "schema_constrained")


def rendered_preview(*args):
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, format="JPEG")
    return buffer.getvalue()
