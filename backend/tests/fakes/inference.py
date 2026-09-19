"""Contract-enforcing inference server with explicit, reproducible wire faults."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

from fastapi import FastAPI, Request
from fastapi.responses import (
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)


@dataclass
class InferenceFake:
    dimension: int = 4
    model: str = "test-embedding"
    fault: str = ""
    calls: list[dict] = field(default_factory=list)
    redirected: int = 0
    failures_remaining: int = 0
    chat_dialect: str = "json_schema"
    chat_result: dict = field(default_factory=lambda: {"caption": "A small boat"})
    chat_malformed_remaining: int = 0
    responses_available: bool = False
    hold_embedding_call: int | None = None

    def app(self) -> FastAPI:
        app = FastAPI()

        @app.post("/v1/embeddings")
        async def embeddings(request: Request):
            body = await request.json()
            self.calls.append(
                {"path": "embeddings", "body": body, "headers": dict(request.headers)}
            )
            if (
                body.get("model") != self.model
                or body.get("encoding_format") != "float"
                or "dimensions" in body
            ):
                return JSONResponse(
                    {"error": {"code": "bad_contract"}}, status_code=422
                )
            inputs = body.get("input")
            if (
                not isinstance(inputs, list)
                or not 1 <= len(inputs) <= 8
                or not all(isinstance(item, str) for item in inputs)
            ):
                return JSONResponse({"error": {"code": "bad_input"}}, status_code=422)
            call_number = len(self.calls)
            while self.hold_embedding_call == call_number:
                await asyncio.sleep(0.05)
            if self.fault == "redirect":
                return RedirectResponse("/redirected", status_code=307)
            if self.fault in {"429", "500", "401"} or self.failures_remaining:
                self.failures_remaining = max(0, self.failures_remaining - 1)
                return JSONResponse(
                    {"error": {"message": "test-secret-must-not-leak"}},
                    status_code=int(self.fault or "429"),
                    headers={"Retry-After": "0"},
                )
            if self.fault == "timeout":
                await asyncio.sleep(2)
            if self.fault == "trickle":

                async def trickle():
                    for _ in range(100):
                        yield b" "
                        await asyncio.sleep(0.02)

                return StreamingResponse(trickle(), media_type="application/json")
            if self.fault == "oversized":
                return Response(b" " * (2 * 1024**2 + 1), media_type="application/json")
            if self.fault == "compressed":
                return Response(b"compressed", headers={"Content-Encoding": "gzip"})
            if self.fault == "json":
                return Response(b"not-json", media_type="application/json")
            data = [
                {
                    "index": index,
                    "embedding": [float(index + 1), 1.0] + [0.0] * (self.dimension - 2),
                }
                for index in range(len(inputs))
            ]
            if self.fault == "missing":
                data.pop()
            elif self.fault == "duplicate":
                data[-1]["index"] = 0
            elif self.fault == "dimension":
                data[0]["embedding"] = [1.0]
            elif self.fault == "zero":
                data[0]["embedding"] = [0.0] * self.dimension
            elif self.fault == "nonfinite":
                return Response(
                    '{"data":[{"index":0,"embedding":[NaN,0,0,0]}]}',
                    media_type="application/json",
                )
            elif self.fault == "boolean":
                data[0]["embedding"][0] = True
            elif self.fault == "index":
                data[0]["index"] = True
            return JSONResponse(
                {"data": list(reversed(data))},
                headers={"X-Test-Response": "test-upstream-secret"},
            )

        @app.post("/redirected")
        async def redirected():
            self.redirected += 1
            return {}

        @app.post("/v1/chat/completions")
        async def chat(request: Request):
            body = await request.json()
            self.calls.append({"path": "chat", "body": body})
            dialect = (
                "tools"
                if "tools" in body
                else "json_schema"
                if body.get("response_format", {}).get("type") == "json_schema"
                else "json"
            )
            unsupported = (
                dialect == "json_schema" and self.chat_dialect != "json_schema"
            ) or (dialect == "tools" and self.chat_dialect == "json")
            if unsupported:
                return JSONResponse(
                    {
                        "error": {
                            "code": "unsupported_parameter",
                            "param": "tools"
                            if dialect == "tools"
                            else "response_format",
                        }
                    },
                    status_code=400,
                )
            if self.fault == "timeout":
                await asyncio.sleep(2)
            if self.fault == "500":
                return JSONResponse(
                    {"error": {"code": "server_error"}},
                    status_code=500,
                    headers={"Retry-After": "0"},
                )
            content = json.dumps(self.chat_result)
            if self.chat_malformed_remaining:
                self.chat_malformed_remaining -= 1
                content = "invalid response"
            message = {"role": "assistant", "content": content}
            if dialect == "tools":
                message = {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": "structured_result",
                                "arguments": content,
                            },
                        }
                    ],
                }
            return {
                "choices": [{"index": 0, "finish_reason": "stop", "message": message}]
            }

        @app.post("/v1/responses")
        async def responses(request: Request):
            body = await request.json()
            self.calls.append({"path": "responses", "body": body})
            if not self.responses_available:
                return JSONResponse({"error": {"code": "not_found"}}, status_code=404)
            if self.fault == "timeout":
                await asyncio.sleep(2)
            if (
                body.get("store") is not False
                or body.get("text", {}).get("format", {}).get("type") != "json_schema"
            ):
                return JSONResponse(
                    {"error": {"code": "bad_contract"}}, status_code=422
                )
            return {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(self.chat_result),
                            }
                        ],
                    }
                ],
            }

        return app
