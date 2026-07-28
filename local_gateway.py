#!/usr/bin/env python3
"""Local Codex CLI and Claude Code adapter for Ringer's pre-call router.

This is a gateway, not a chat client. Codex CLI can send OpenAI Responses
requests to it. The Anthropic Messages adapter is experimental and is not yet
compatible with the retries and streaming behavior of the real Claude Code
client.
Ringer answers exact local work without an upstream model call. When a model
is needed, Ringer sends only the selected source packet and newest user request.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from context_packet import build_context_packet
from precall_router import (
    ModelCall,
    ModelResponse,
    ModelUsage,
    PreCallRouter,
    RouterLimits,
    RouterRequest,
    RouterStore,
    TokenValue,
    _cache_key,
    estimate_tokens,
)


MAX_REQUEST_BYTES = 10 * 1024 * 1024
DEFAULT_PORT = 8790
DEFAULT_MAX_PACKET_BYTES = 16_000
SAFE_MODEL_ID = "ringer-local"


class GatewayError(RuntimeError):
    """A safe error whose text contains no provider response body or secret."""


def _split_paths(value: str | None) -> tuple[Path, ...]:
    if not value:
        return ()
    return tuple(
        Path(item).expanduser().resolve()
        for item in value.split(os.pathsep)
        if item.strip()
    )


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value.strip() else None


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 0:
        raise ValueError(f"{name} must not be negative")
    return value


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str | None
    api_key: str | None
    cheap_model: str | None
    strong_model: str | None

    def model_for(self, route: str) -> str | None:
        if route == "cheap_model":
            return self.cheap_model
        if route == "strong_model":
            return self.strong_model
        raise ValueError(f"unsupported model route: {route}")


@dataclass(frozen=True)
class GatewayConfig:
    host: str
    port: int
    store_path: Path
    sources: tuple[Path, ...]
    state_files: tuple[Path, ...]
    max_packet_bytes: int
    limits: RouterLimits
    openai: ProviderConfig
    anthropic: ProviderConfig

    @classmethod
    def from_env(cls) -> "GatewayConfig":
        state_root = Path(
            os.environ.get("RINGER_STATE_DIR", "~/.ringer")
        ).expanduser().resolve()
        return cls(
            host=os.environ.get("RINGER_GATEWAY_HOST", "127.0.0.1"),
            port=_env_int("RINGER_GATEWAY_PORT", DEFAULT_PORT),
            store_path=Path(
                os.environ.get(
                    "RINGER_GATEWAY_STORE",
                    str(state_root / "gateway.sqlite3"),
                )
            ).expanduser().resolve(),
            sources=_split_paths(os.environ.get("RINGER_GATEWAY_SOURCES")),
            state_files=_split_paths(
                os.environ.get("RINGER_GATEWAY_STATE_FILES")
            ),
            max_packet_bytes=_env_int(
                "RINGER_GATEWAY_MAX_PACKET_BYTES",
                DEFAULT_MAX_PACKET_BYTES,
            ),
            limits=RouterLimits(
                max_fresh_input_tokens=_env_int(
                    "RINGER_GATEWAY_MAX_FRESH_INPUT_TOKENS", 12_000
                ),
                max_reused_input_tokens=_env_int(
                    "RINGER_GATEWAY_MAX_REUSED_INPUT_TOKENS", 5_000
                ),
                max_output_tokens=_env_int(
                    "RINGER_GATEWAY_MAX_OUTPUT_TOKENS", 4_000
                ),
                max_calls=1,
            ),
            openai=ProviderConfig(
                base_url=_optional_env("RINGER_OPENAI_BASE_URL"),
                api_key=_optional_env("RINGER_OPENAI_API_KEY"),
                cheap_model=_optional_env("RINGER_OPENAI_CHEAP_MODEL"),
                strong_model=_optional_env("RINGER_OPENAI_STRONG_MODEL"),
            ),
            anthropic=ProviderConfig(
                base_url=_optional_env("RINGER_ANTHROPIC_BASE_URL"),
                api_key=_optional_env("RINGER_ANTHROPIC_API_KEY"),
                cheap_model=_optional_env("RINGER_ANTHROPIC_CHEAP_MODEL"),
                strong_model=_optional_env("RINGER_ANTHROPIC_STRONG_MODEL"),
            ),
        )

    def validate(self) -> None:
        parsed_host = self.host.strip().lower()
        if parsed_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError(
                "Ringer gateway is local-only by default; host must be a loopback address"
            )
        if not 0 <= self.port <= 65_535:
            raise ValueError("gateway port must be between 0 and 65535")
        if self.max_packet_bytes < 1_024:
            raise ValueError("gateway packet limit must be at least 1024 bytes")
        for name, provider in (
            ("OpenAI", self.openai),
            ("Anthropic", self.anthropic),
        ):
            configured = (
                provider.base_url,
                provider.api_key,
                provider.cheap_model,
                provider.strong_model,
            )
            if any(configured) and not all(configured):
                raise ValueError(
                    f"{name} upstream needs a base URL, API key, cheap model, "
                    "and strong model; remove all four values for local-only mode"
                )


def _text_from_content(content: Any) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for part in content:
        if not isinstance(part, Mapping):
            continue
        if part.get("type") not in {"input_text", "text"}:
            continue
        text = part.get("text")
        if isinstance(text, str):
            parts.append(text)
    if not parts:
        return None
    return "\n".join(parts)


def newest_user_text(payload: Mapping[str, Any]) -> str:
    """Return the newest user message exactly, without prior conversation."""
    input_value = payload.get("input")
    if isinstance(input_value, str):
        if input_value.strip():
            return input_value
        raise ValueError("the newest user request is empty")
    messages = input_value
    if not isinstance(messages, list):
        messages = payload.get("messages")
    if not isinstance(messages, list):
        raise ValueError("request does not contain an input or messages list")
    for item in reversed(messages):
        if not isinstance(item, Mapping) or item.get("role") != "user":
            continue
        text = _text_from_content(item.get("content"))
        if text is not None and text.strip():
            return text
    raise ValueError("request does not contain a nonempty user message")


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _validate_upstream_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise GatewayError("the configured upstream URL is invalid")


PostJSON = Callable[
    [str, Mapping[str, str], Mapping[str, Any], float],
    Mapping[str, Any],
]


def post_json_once(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout: float,
) -> Mapping[str, Any]:
    """Make one request. Deliberately has no retry or response-body logging."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **dict(headers)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise GatewayError(
            f"upstream returned HTTP {exc.code}; Ringer did not retry"
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise GatewayError(
            "upstream could not be reached; Ringer did not retry"
        ) from None
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GatewayError("upstream returned invalid JSON; Ringer did not retry") from None
    if not isinstance(parsed, Mapping):
        raise GatewayError("upstream returned an invalid response; Ringer did not retry")
    return parsed


def _openai_answer(payload: Mapping[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str):
        return direct
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, Mapping):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for part in content:
                if (
                    isinstance(part, Mapping)
                    and part.get("type") in {"output_text", "text"}
                    and isinstance(part.get("text"), str)
                ):
                    parts.append(str(part["text"]))
        if parts:
            return "".join(parts)
    raise GatewayError("upstream returned no text answer; Ringer did not retry")


def _openai_usage(payload: Mapping[str, Any]) -> ModelUsage:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return ModelUsage()
    total_input = int(usage.get("input_tokens") or 0)
    input_details = usage.get("input_tokens_details")
    cached = (
        int(input_details.get("cached_tokens") or 0)
        if isinstance(input_details, Mapping)
        else 0
    )
    output = int(usage.get("output_tokens") or 0)
    output_details = usage.get("output_tokens_details")
    reasoning = (
        int(output_details.get("reasoning_tokens") or 0)
        if isinstance(output_details, Mapping)
        else 0
    )
    return ModelUsage(
        fresh_input=TokenValue.reported(max(0, total_input - cached)),
        reused_input=TokenValue.reported(max(0, cached)),
        cache_write_input=TokenValue.reported(0),
        output=TokenValue.reported(max(0, output)),
        reasoning=TokenValue.reported(max(0, reasoning)),
    )


def _anthropic_answer(payload: Mapping[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, list):
        parts = [
            str(item["text"])
            for item in content
            if isinstance(item, Mapping)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ]
        if parts:
            return "".join(parts)
    raise GatewayError("upstream returned no text answer; Ringer did not retry")


def _anthropic_usage(payload: Mapping[str, Any]) -> ModelUsage:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        return ModelUsage()
    return ModelUsage(
        fresh_input=TokenValue.reported(int(usage.get("input_tokens") or 0)),
        reused_input=TokenValue.reported(
            int(usage.get("cache_read_input_tokens") or 0)
        ),
        cache_write_input=TokenValue.reported(
            int(usage.get("cache_creation_input_tokens") or 0)
        ),
        output=TokenValue.reported(int(usage.get("output_tokens") or 0)),
        reasoning=TokenValue.reported(0),
    )


class OpenAIExecutor:
    estimated_input_overhead_tokens = 0
    estimated_reused_input_tokens = 0

    def __init__(
        self,
        *,
        config: ProviderConfig,
        route: str,
        post_json: PostJSON = post_json_once,
    ) -> None:
        model = config.model_for(route)
        if not config.base_url or not config.api_key or not model:
            raise ValueError(f"no OpenAI {route.replace('_', ' ')} is configured")
        _validate_upstream_url(config.base_url)
        self.base_url = config.base_url
        self.api_key = config.api_key
        self.model_name = model
        self.post_json = post_json

    def __call__(self, call: ModelCall) -> ModelResponse:
        payload = {
            "model": self.model_name,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": call.packet_text}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": call.user_text}
                    ],
                },
            ],
            "max_output_tokens": call.max_output_tokens,
            "stream": False,
            "store": False,
        }
        response = self.post_json(
            _join_url(self.base_url, "responses"),
            {"Authorization": f"Bearer {self.api_key}"},
            payload,
            120.0,
        )
        return ModelResponse(
            answer=_openai_answer(response),
            usage=_openai_usage(response),
        )


class AnthropicExecutor:
    estimated_input_overhead_tokens = 0
    estimated_reused_input_tokens = 0

    def __init__(
        self,
        *,
        config: ProviderConfig,
        route: str,
        post_json: PostJSON = post_json_once,
    ) -> None:
        model = config.model_for(route)
        if not config.base_url or not config.api_key or not model:
            raise ValueError(f"no Anthropic {route.replace('_', ' ')} is configured")
        _validate_upstream_url(config.base_url)
        self.base_url = config.base_url
        self.api_key = config.api_key
        self.model_name = model
        self.post_json = post_json

    def __call__(self, call: ModelCall) -> ModelResponse:
        payload = {
            "model": self.model_name,
            "system": call.packet_text,
            "messages": [{"role": "user", "content": call.user_text}],
            "max_tokens": call.max_output_tokens,
            "stream": False,
        }
        response = self.post_json(
            _join_url(self.base_url, "messages"),
            {
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
            payload,
            120.0,
        )
        return ModelResponse(
            answer=_anthropic_answer(response),
            usage=_anthropic_usage(response),
        )


@dataclass(frozen=True)
class GatewayReply:
    answer: str
    route: str
    model: str
    usage: ModelUsage
    received_bytes: int
    forwarded_bytes: int
    upstream_calls: int


class GatewayApp:
    def __init__(
        self,
        config: GatewayConfig,
        *,
        post_json: PostJSON = post_json_once,
    ) -> None:
        config.validate()
        self.config = config
        self.store = RouterStore(config.store_path)
        self.post_json = post_json

    def accept_exact(self, user_text: str, answer: str) -> str:
        """Save one reviewed answer for one exact request and source packet."""
        if not user_text.strip():
            raise ValueError("accepted request must not be empty")
        if not answer.strip():
            raise ValueError("accepted answer must not be empty")
        request = RouterRequest(
            text=user_text,
            sources=self.config.sources,
            state_files=self.config.state_files,
            max_packet_bytes=self.config.max_packet_bytes,
        )
        packet = build_context_packet(
            request.text,
            sources=request.sources,
            state_files=request.state_files,
            max_packet_bytes=request.max_packet_bytes,
            max_file_bytes=request.max_file_bytes,
            max_files=request.max_files,
        )
        cache_key = _cache_key(request, packet)
        self.store.accept(
            cache_key=cache_key,
            answer=answer,
            origin_route="manual_accept",
            origin_reason="The user explicitly saved this reviewed answer.",
        )
        return cache_key

    def _executors(
        self,
        provider_name: str,
    ) -> tuple[OpenAIExecutor | AnthropicExecutor | None, OpenAIExecutor | AnthropicExecutor | None]:
        provider = (
            self.config.openai
            if provider_name == "openai"
            else self.config.anthropic
        )
        if not provider.base_url:
            return None, None
        executor_type = (
            OpenAIExecutor if provider_name == "openai" else AnthropicExecutor
        )
        return (
            executor_type(
                config=provider,
                route="cheap_model",
                post_json=self.post_json,
            ),
            executor_type(
                config=provider,
                route="strong_model",
                post_json=self.post_json,
            ),
        )

    def handle(
        self,
        provider_name: str,
        payload: Mapping[str, Any],
        *,
        received_bytes: int | None = None,
    ) -> GatewayReply:
        user_text = newest_user_text(payload)
        cheap, strong = self._executors(provider_name)
        router = PreCallRouter(
            store=self.store,
            cheap_executor=cheap,
            strong_executor=strong,
            limits=self.config.limits,
            auto_accept=False,
        )
        result = router.route(
            RouterRequest(
                text=user_text,
                sources=self.config.sources,
                state_files=self.config.state_files,
                max_packet_bytes=self.config.max_packet_bytes,
            )
        )
        if not result.ok or result.answer is None:
            raise GatewayError(result.reason)
        if result.route == "local_code":
            # Deterministic recipes are safe to remember. Model answers are not
            # accepted here because the user has not reviewed them yet.
            self.store.accept(
                cache_key=result.cache_key,
                answer=result.answer,
                origin_route=result.route,
                origin_reason=result.reason,
            )
        usage = result.usage or ModelUsage(
            fresh_input=TokenValue.reported(0),
            reused_input=TokenValue.reported(0),
            cache_write_input=TokenValue.reported(0),
            output=TokenValue.reported(0),
            reasoning=TokenValue.reported(0),
        )
        model = SAFE_MODEL_ID
        if result.route == "cheap_model" and cheap is not None:
            model = cheap.model_name
        elif result.route == "strong_model" and strong is not None:
            model = strong.model_name
        forwarded_bytes = 0
        if result.model_calls:
            fresh = usage.fresh_input.value or 0
            forwarded_bytes = fresh * 4
        return GatewayReply(
            answer=result.answer,
            route=result.route,
            model=model,
            usage=usage,
            received_bytes=(
                received_bytes
                if received_bytes is not None
                else len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            ),
            forwarded_bytes=forwarded_bytes,
            upstream_calls=result.model_calls,
        )


def _token_value(value: TokenValue) -> int:
    return value.value or 0


def openai_response(reply: GatewayReply, requested_model: str) -> dict[str, Any]:
    response_id = f"resp_{uuid.uuid4().hex}"
    message_id = f"msg_{uuid.uuid4().hex}"
    fresh = _token_value(reply.usage.fresh_input)
    reused = _token_value(reply.usage.reused_input)
    output = _token_value(reply.usage.output)
    reasoning = _token_value(reply.usage.reasoning)
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time.time()),
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "instructions": None,
        "max_output_tokens": None,
        "model": requested_model or reply.model,
        "output": [
            {
                "id": message_id,
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": reply.answer,
                        "annotations": [],
                    }
                ],
            }
        ],
        "parallel_tool_calls": True,
        "previous_response_id": None,
        "reasoning": {"effort": "low", "summary": None},
        "store": False,
        "temperature": None,
        "text": {"format": {"type": "text"}, "verbosity": "low"},
        "tool_choice": "auto",
        "tools": [],
        "top_p": None,
        "truncation": "disabled",
        "usage": {
            "input_tokens": fresh + reused,
            "input_tokens_details": {"cached_tokens": reused},
            "output_tokens": output,
            "output_tokens_details": {"reasoning_tokens": reasoning},
            "total_tokens": fresh + reused + output,
        },
        "user": None,
        "metadata": {
            "ringer_route": reply.route,
            "ringer_upstream_calls": str(reply.upstream_calls),
        },
    }


def anthropic_response(reply: GatewayReply) -> dict[str, Any]:
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": reply.model,
        "content": [{"type": "text", "text": reply.answer}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {
            "input_tokens": _token_value(reply.usage.fresh_input),
            "cache_creation_input_tokens": _token_value(
                reply.usage.cache_write_input
            ),
            "cache_read_input_tokens": _token_value(reply.usage.reused_input),
            "output_tokens": _token_value(reply.usage.output),
        },
    }


def openai_sse(response: Mapping[str, Any]) -> bytes:
    response_id = str(response["id"])
    model = str(response["model"])
    answer = str(response["output"][0]["content"][0]["text"])  # type: ignore[index]
    message_id = str(response["output"][0]["id"])  # type: ignore[index]
    created = dict(response)
    created["status"] = "in_progress"
    created["output"] = []
    created["usage"] = None
    item = {
        "id": message_id,
        "type": "message",
        "status": "in_progress",
        "role": "assistant",
        "content": [],
    }
    events = [
        {
            "type": "response.created",
            "sequence_number": 0,
            "response": created,
        },
        {
            "type": "response.in_progress",
            "sequence_number": 1,
            "response": created,
        },
        {
            "type": "response.output_item.added",
            "sequence_number": 2,
            "output_index": 0,
            "item": item,
        },
        {
            "type": "response.content_part.added",
            "sequence_number": 3,
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "part": {"type": "output_text", "text": "", "annotations": []},
        },
        {
            "type": "response.output_text.delta",
            "sequence_number": 4,
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "delta": answer,
            "logprobs": [],
        },
        {
            "type": "response.output_text.done",
            "sequence_number": 5,
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "text": answer,
            "logprobs": [],
        },
        {
            "type": "response.content_part.done",
            "sequence_number": 6,
            "item_id": message_id,
            "output_index": 0,
            "content_index": 0,
            "part": {
                "type": "output_text",
                "text": answer,
                "annotations": [],
            },
        },
        {
            "type": "response.output_item.done",
            "sequence_number": 7,
            "output_index": 0,
            "item": response["output"][0],
        },
        {
            "type": "response.completed",
            "sequence_number": 8,
            "response": response,
        },
    ]
    chunks = [
        (
            f"event: {event['type']}\n"
            f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        )
        for event in events
    ]
    return "".join(chunks).encode("utf-8")


class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "RingerGateway/0.1"

    @property
    def app(self) -> GatewayApp:
        return self.server.app  # type: ignore[attr-defined]

    def _send_json(
        self,
        status: int,
        payload: Mapping[str, Any],
        *,
        route: str | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if route:
            self.send_header("X-Ringer-Route", route)
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> tuple[Mapping[str, Any], int]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("invalid Content-Length") from None
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ValueError("request body size is outside the allowed range")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("request body is not valid JSON") from None
        if not isinstance(parsed, Mapping):
            raise ValueError("request body must be a JSON object")
        return parsed, len(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        if path == "/health":
            self._send_json(
                HTTPStatus.OK,
                {"status": "ok", "local_only": True},
            )
            return
        if path == "/v1/models":
            self._send_json(
                HTTPStatus.OK,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": SAFE_MODEL_ID,
                            "object": "model",
                            "created": int(time.time()),
                            "owned_by": "ringer",
                        }
                    ],
                    "models": [
                        {
                            "slug": SAFE_MODEL_ID,
                            "id": SAFE_MODEL_ID,
                            "display_name": "Ringer local gateway",
                            "description": (
                                "Local Ringer gateway for smaller Codex requests"
                            ),
                            "default_reasoning_level": "low",
                            "supported_reasoning_levels": [
                                {
                                    "effort": "low",
                                    "description": (
                                        "Fast responses with lighter reasoning"
                                    ),
                                }
                            ],
                            "shell_type": "shell_command",
                            "visibility": "list",
                            "supported_in_api": True,
                            "priority": 1,
                            "additional_speed_tiers": [],
                            "service_tiers": [
                                {
                                    "id": "priority",
                                    "name": "Priority",
                                    "description": "Configured client tier",
                                }
                            ],
                            "availability_nux": None,
                            "upgrade": None,
                            "base_instructions": (
                                "Answer the newest user request directly."
                            ),
                            "include_skills_usage_instructions": False,
                            "default_reasoning_summary": "none",
                            "support_verbosity": True,
                            "default_verbosity": "low",
                            "apply_patch_tool_type": "freeform",
                            "web_search_tool_type": "text_and_image",
                            "truncation_policy": {
                                "mode": "tokens",
                                "limit": 10_000,
                            },
                            "supports_parallel_tool_calls": True,
                            "supports_image_detail_original": False,
                            "context_window": 128_000,
                            "max_context_window": 128_000,
                            "comp_hash": "ringer-local-v1",
                            "effective_context_window_percent": 95,
                            "experimental_supported_tools": [],
                            "input_modalities": ["text"],
                            "supports_search_tool": False,
                            "use_responses_lite": True,
                            "tool_mode": "code_mode",
                            "multi_agent_version": "v2",
                        }
                    ],
                },
            )
            return
        self._send_json(
            HTTPStatus.NOT_FOUND,
            {"error": {"code": "not_found", "message": "endpoint not found"}},
        )

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/")
        try:
            payload, received_bytes = self._read_json()
            if path == "/v1/responses":
                reply = self.app.handle(
                    "openai",
                    payload,
                    received_bytes=received_bytes,
                )
                rendered = openai_response(
                    reply,
                    str(payload.get("model") or SAFE_MODEL_ID),
                )
                if payload.get("stream") is True:
                    body = openai_sse(rendered)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.send_header("X-Ringer-Route", reply.route)
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send_json(
                        HTTPStatus.OK,
                        rendered,
                        route=reply.route,
                    )
                return
            if path == "/v1/messages":
                reply = self.app.handle(
                    "anthropic",
                    payload,
                    received_bytes=received_bytes,
                )
                self._send_json(
                    HTTPStatus.OK,
                    anthropic_response(reply),
                    route=reply.route,
                )
                return
            if path == "/v1/messages/count_tokens":
                text = newest_user_text(payload)
                self._send_json(
                    HTTPStatus.OK,
                    {"input_tokens": estimate_tokens(text)},
                    route="local_code",
                )
                return
            self._send_json(
                HTTPStatus.NOT_FOUND,
                {
                    "error": {
                        "code": "not_found",
                        "message": "endpoint not found",
                    }
                },
            )
        except ValueError as exc:
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "invalid_request", "message": str(exc)}},
            )
        except GatewayError as exc:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "ringer_stopped", "message": str(exc)}},
            )

    def log_message(self, _format: str, *_args: Any) -> None:
        # Request URLs can include query strings. Do not emit them or any body.
        return


def build_server(
    config: GatewayConfig,
    *,
    post_json: PostJSON = post_json_once,
) -> ThreadingHTTPServer:
    app = GatewayApp(config, post_json=post_json)
    server = ThreadingHTTPServer((config.host, config.port), GatewayHandler)
    server.app = app  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Use Ringer from Codex CLI while Ringer removes old conversation "
            "before an upstream model call."
        )
    )
    parser.add_argument("--host", help="loopback host; default: 127.0.0.1")
    parser.add_argument("--port", type=int, help=f"local port; default: {DEFAULT_PORT}")
    parser.add_argument(
        "--accept-request-file",
        type=Path,
        help="save an exact reviewed request without starting the server",
    )
    parser.add_argument(
        "--accept-answer-file",
        type=Path,
        help="save its reviewed answer without starting the server",
    )
    args = parser.parse_args(argv)
    config = GatewayConfig.from_env()
    if args.host is not None or args.port is not None:
        config = GatewayConfig(
            host=args.host or config.host,
            port=args.port if args.port is not None else config.port,
            store_path=config.store_path,
            sources=config.sources,
            state_files=config.state_files,
            max_packet_bytes=config.max_packet_bytes,
            limits=config.limits,
            openai=config.openai,
            anthropic=config.anthropic,
        )
    if bool(args.accept_request_file) != bool(args.accept_answer_file):
        parser.error(
            "--accept-request-file and --accept-answer-file must be used together"
        )
    if args.accept_request_file and args.accept_answer_file:
        request_text = args.accept_request_file.read_text(encoding="utf-8")
        answer_text = args.accept_answer_file.read_text(encoding="utf-8")
        app = GatewayApp(config)
        cache_key = app.accept_exact(request_text, answer_text)
        print(f"Saved reviewed answer for exact request: {cache_key}")
        return 0
    server = build_server(config)
    print(
        f"Ringer gateway listening on http://{config.host}:{server.server_port}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
