from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Mapping

from local_gateway import (
    GatewayApp,
    GatewayConfig,
    GatewayError,
    ProviderConfig,
    build_server,
    newest_user_text,
)
from precall_router import RouterLimits


def provider(
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    cheap_model: str | None = None,
    strong_model: str | None = None,
) -> ProviderConfig:
    return ProviderConfig(
        base_url=base_url,
        api_key=api_key,
        cheap_model=cheap_model,
        strong_model=strong_model,
    )


class GatewayTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(
        self,
        *,
        sources: tuple[Path, ...] = (),
        openai: ProviderConfig | None = None,
        anthropic: ProviderConfig | None = None,
    ) -> GatewayConfig:
        return GatewayConfig(
            host="127.0.0.1",
            port=0,
            store_path=self.root / "gateway.sqlite3",
            sources=sources,
            state_files=(),
            max_packet_bytes=16_000,
            limits=RouterLimits(
                max_fresh_input_tokens=12_000,
                max_reused_input_tokens=5_000,
                max_output_tokens=1_000,
                max_calls=1,
            ),
            openai=openai or provider(),
            anthropic=anthropic or provider(),
        )

    def test_newest_user_text_ignores_old_messages_and_preserves_whitespace(self) -> None:
        payload = {
            "input": [
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Old request."}
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "Old answer."}
                    ],
                },
                {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "  Calculate 19 * 3.  ",
                        }
                    ],
                },
            ]
        }

        self.assertEqual(
            "  Calculate 19 * 3.  ",
            newest_user_text(payload),
        )

    def test_local_recipe_answers_normal_responses_request_with_no_upstream(self) -> None:
        upstream_calls: list[object] = []

        def should_not_call(*args: object) -> Mapping[str, Any]:
            upstream_calls.append(args)
            raise AssertionError("local recipe must not call upstream")

        app = GatewayApp(self.config(), post_json=should_not_call)
        old_history = "OLD EXPENSIVE HISTORY " * 8_000
        payload = {
            "model": "gpt-5.6-sol",
            "stream": True,
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": old_history}
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": "Calculate 144 / 12"}
                    ],
                },
            ],
            "tools": [{"type": "function", "name": "huge_tool"}] * 100,
        }

        reply = app.handle("openai", payload)

        self.assertEqual("12", reply.answer)
        self.assertEqual("local_code", reply.route)
        self.assertEqual(0, reply.upstream_calls)
        self.assertEqual([], upstream_calls)

        cached = app.handle("openai", payload)

        self.assertEqual("12", cached.answer)
        self.assertEqual("accepted_cache", cached.route)
        self.assertEqual(0, cached.upstream_calls)
        self.assertEqual([], upstream_calls)

    def test_model_route_forwards_only_selected_packet_and_latest_request(self) -> None:
        source = self.root / "source.md"
        source.write_text(
            ("Irrelevant background.\n" * 20_000)
            + "Launch decision: ship Wednesday with a smaller scope.\n",
            encoding="utf-8",
        )
        calls: list[dict[str, Any]] = []

        def fake_post(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            timeout: float,
        ) -> Mapping[str, Any]:
            calls.append(
                {
                    "url": url,
                    "headers": dict(headers),
                    "payload": dict(payload),
                    "timeout": timeout,
                }
            )
            return {
                "id": "resp_upstream",
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Ship Wednesday with a smaller scope.",
                            }
                        ],
                    }
                ],
                "usage": {
                    "input_tokens": 600,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens": 10,
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }

        app = GatewayApp(
            self.config(
                sources=(source,),
                openai=provider(
                    base_url="https://provider.example/v1",
                    api_key="secret-test-key",
                    cheap_model="cheap-test",
                    strong_model="strong-test",
                ),
            ),
            post_json=fake_post,
        )
        old_history = "OLD HISTORY MUST NOT BE FORWARDED. " * 20_000
        latest = "  Summarize the launch decision in one sentence.  "
        incoming = {
            "model": "gpt-5.6-sol",
            "input": [
                {
                    "role": "developer",
                    "content": [
                        {"type": "input_text", "text": old_history}
                    ],
                },
                {
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": old_history}
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": latest}],
                },
            ],
            "tools": [
                {
                    "type": "function",
                    "name": f"tool_{number}",
                    "description": old_history[:5_000],
                }
                for number in range(30)
            ],
        }

        reply = app.handle("openai", incoming)

        self.assertEqual("cheap_model", reply.route)
        self.assertEqual(1, reply.upstream_calls)
        self.assertEqual(1, len(calls))
        forwarded = calls[0]["payload"]
        forwarded_text = json.dumps(forwarded, ensure_ascii=False)
        incoming_text = json.dumps(incoming, ensure_ascii=False)
        self.assertNotIn("OLD HISTORY MUST NOT BE FORWARDED", forwarded_text)
        self.assertIn(latest, forwarded_text)
        self.assertIn("ship Wednesday with a smaller scope", forwarded_text)
        self.assertLess(len(forwarded_text), len(incoming_text) * 0.1)
        self.assertEqual(
            "Bearer secret-test-key",
            calls[0]["headers"]["Authorization"],
        )
        self.assertNotIn("tools", forwarded)

    def test_explicitly_accepted_answer_skips_upstream_model(self) -> None:
        upstream_calls: list[object] = []

        def should_not_call(*args: object) -> Mapping[str, Any]:
            upstream_calls.append(args)
            raise AssertionError("accepted answer must not call upstream")

        app = GatewayApp(self.config(), post_json=should_not_call)
        request = "Research and write a thesis about this result."
        answer = "The reviewed thesis."

        cache_key = app.accept_exact(request, answer)
        reply = app.handle("openai", {"input": request})

        self.assertEqual(64, len(cache_key))
        self.assertEqual("accepted_cache", reply.route)
        self.assertEqual(answer, reply.answer)
        self.assertEqual(0, reply.upstream_calls)
        self.assertEqual([], upstream_calls)

    def test_failed_upstream_is_called_once_and_does_not_fall_back(self) -> None:
        call_count = 0

        def fail_once(
            _url: str,
            _headers: Mapping[str, str],
            _payload: Mapping[str, Any],
            _timeout: float,
        ) -> Mapping[str, Any]:
            nonlocal call_count
            call_count += 1
            raise GatewayError("upstream failed; Ringer did not retry")

        app = GatewayApp(
            self.config(
                openai=provider(
                    base_url="https://provider.example/v1",
                    api_key="secret-test-key",
                    cheap_model="cheap-test",
                    strong_model="strong-test",
                )
            ),
            post_json=fail_once,
        )

        with self.assertRaisesRegex(GatewayError, "did not retry"):
            app.handle(
                "openai",
                {"input": "Summarize this short request."},
            )

        self.assertEqual(1, call_count)

    def test_anthropic_model_route_uses_clean_messages_body(self) -> None:
        calls: list[dict[str, Any]] = []

        def fake_post(
            url: str,
            headers: Mapping[str, str],
            payload: Mapping[str, Any],
            _timeout: float,
        ) -> Mapping[str, Any]:
            calls.append(
                {"url": url, "headers": dict(headers), "payload": dict(payload)}
            )
            return {
                "id": "msg_upstream",
                "content": [{"type": "text", "text": "A short answer."}],
                "usage": {
                    "input_tokens": 40,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 4,
                },
            }

        app = GatewayApp(
            self.config(
                anthropic=provider(
                    base_url="https://api.anthropic.com/v1",
                    api_key="anthropic-test-key",
                    cheap_model="claude-cheap",
                    strong_model="claude-strong",
                )
            ),
            post_json=fake_post,
        )
        old_history = "OLD CLAUDE HISTORY " * 10_000

        reply = app.handle(
            "anthropic",
            {
                "model": "claude-user-choice",
                "system": old_history,
                "messages": [
                    {"role": "user", "content": "Old question"},
                    {"role": "assistant", "content": old_history},
                    {"role": "user", "content": "Summarize this request."},
                ],
                "tools": [{"name": "old_tool", "description": old_history}],
            },
        )

        self.assertEqual("cheap_model", reply.route)
        self.assertEqual(1, len(calls))
        forwarded_text = json.dumps(calls[0]["payload"])
        self.assertNotIn("OLD CLAUDE HISTORY", forwarded_text)
        self.assertIn("Summarize this request.", forwarded_text)
        self.assertNotIn("tools", calls[0]["payload"])

    def test_http_responses_endpoint_returns_sse_local_answer(self) -> None:
        calls: list[object] = []

        def should_not_call(*args: object) -> Mapping[str, Any]:
            calls.append(args)
            raise AssertionError("local recipe must not call upstream")

        server = build_server(self.config(), post_json=should_not_call)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection(
                "127.0.0.1",
                server.server_port,
                timeout=5,
            )
            body = json.dumps(
                {
                    "model": "gpt-5.6-sol",
                    "stream": True,
                    "input": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_text",
                                    "text": "Calculate 7 * 8",
                                }
                            ],
                        }
                    ],
                }
            )
            connection.request(
                "POST",
                "/v1/responses",
                body=body,
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            response_body = response.read().decode("utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.assertEqual(200, response.status)
        self.assertEqual("local_code", response.getheader("X-Ringer-Route"))
        self.assertIn("event: response.in_progress", response_body)
        self.assertIn("event: response.output_text.delta", response_body)
        self.assertIn('"delta":"56"', response_body)
        self.assertIn("event: response.content_part.done", response_body)
        self.assertIn("event: response.output_item.done", response_body)
        self.assertIn("event: response.completed", response_body)
        self.assertEqual([], calls)

    def test_non_loopback_bind_is_rejected(self) -> None:
        config = self.config()
        unsafe = GatewayConfig(
            host="0.0.0.0",
            port=config.port,
            store_path=config.store_path,
            sources=config.sources,
            state_files=config.state_files,
            max_packet_bytes=config.max_packet_bytes,
            limits=config.limits,
            openai=config.openai,
            anthropic=config.anthropic,
        )

        with self.assertRaisesRegex(ValueError, "local-only"):
            GatewayApp(unsafe)


if __name__ == "__main__":
    unittest.main()
