"""Structured ChatProvider stand-in; captures observable egress, never parses for us."""

from printstash_core.inference.chat import ChatResult

from app.modules.inference.endpoint import EndpointConfig


def parsed_output(**filters):
    return {
        "residual_query": "bracket",
        "sort": "relevance",
        "filters": {
            "collection_id": None,
            "printer_id": None,
            "tag": [],
            "material_type": [],
            "printed": None,
            "print_outcome": [],
            "printed_period": None,
            "printed_after": None,
            "printed_before": None,
            "print_duration_min_s": None,
            "print_duration_max_s": None,
            **filters,
        },
    }


class ParseProvider:
    def __init__(self, value=None, *, error=None, before_reply=None):
        self.endpoint = EndpointConfig(
            base_url="http://127.0.0.1:9988/v1",
            model="local-chat",
            revision="fixture-v1",
        )
        self.value = value if value is not None else parsed_output()
        self.error, self.before_reply = error, before_reply
        self.requests = []

    def complete(self, request, *, context=None):
        self.requests.append(request)
        if self.before_reply:
            self.before_reply()
        if self.error:
            raise self.error
        return ChatResult(self.value, "json_schema", "schema_constrained")
