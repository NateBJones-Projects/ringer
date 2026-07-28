#!/usr/bin/env python3
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from precall_router import (
    ModelCall,
    ModelResponse,
    ModelUsage,
    PreCallRouter,
    RouterLimits,
    RouterRequest,
    RouterStore,
    TokenValue,
)


def reported_usage(
    *,
    fresh: int = 100,
    reused: int = 0,
    cache_write: int = 0,
    output: int = 20,
    reasoning: int = 0,
) -> ModelUsage:
    return ModelUsage(
        fresh_input=TokenValue.reported(fresh),
        reused_input=TokenValue.reported(reused),
        cache_write_input=TokenValue.reported(cache_write),
        output=TokenValue.reported(output),
        reasoning=TokenValue.reported(reasoning),
    )


class FakeExecutor:
    def __init__(
        self,
        *,
        answer: str = "A useful answer.",
        usage: ModelUsage | None = None,
        model_name: str = "fake/model",
        overhead: int = 0,
        reused_estimate: int | None = 0,
        error: Exception | None = None,
    ) -> None:
        self.answer = answer
        self.usage = usage or reported_usage()
        self.model_name = model_name
        self.estimated_input_overhead_tokens = overhead
        if reused_estimate is not None:
            self.estimated_reused_input_tokens = reused_estimate
        self.error = error
        self.calls: list[ModelCall] = []

    def __call__(self, call: ModelCall) -> ModelResponse:
        self.calls.append(call)
        if self.error is not None:
            raise self.error
        return ModelResponse(answer=self.answer, usage=self.usage)


class PreCallRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_root = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_root.cleanup)
        self.root = Path(self.temp_root.name)
        self.store = RouterStore(self.root / "router.sqlite3")

    def router(
        self,
        *,
        cheap: FakeExecutor | None = None,
        strong: FakeExecutor | None = None,
        limits: RouterLimits | None = None,
        auto_accept: bool = True,
    ) -> PreCallRouter:
        return PreCallRouter(
            store=self.store,
            cheap_executor=cheap,
            strong_executor=strong,
            limits=limits,
            auto_accept=auto_accept,
        )

    def test_exact_accepted_result_skips_the_second_model_call(self) -> None:
        cheap = FakeExecutor(answer="Ship Wednesday.")
        router = self.router(cheap=cheap, strong=FakeExecutor())
        request = RouterRequest("Summarize the decision in one sentence.")

        first = router.route(request)
        second = router.route(request)

        self.assertEqual("cheap_model", first.route)
        self.assertEqual("accepted_cache", second.route)
        self.assertEqual(first.answer, second.answer)
        self.assertEqual(1, len(cheap.calls))
        self.assertEqual(1, self.store.model_call_count())
        self.assertEqual(1, self.store.accepted_count())

    def test_changed_selected_source_does_not_hit_the_cache(self) -> None:
        source = self.root / "decision.md"
        source.write_text("The decision is Wednesday.\n", encoding="utf-8")
        cheap = FakeExecutor(answer="Wednesday.")
        router = self.router(cheap=cheap, strong=FakeExecutor())
        request = RouterRequest(
            "Summarize the decision.",
            sources=(source,),
        )

        first = router.route(request)
        source.write_text("The decision is Thursday.\n", encoding="utf-8")
        second = router.route(request)

        self.assertEqual("cheap_model", first.route)
        self.assertEqual("cheap_model", second.route)
        self.assertEqual(2, len(cheap.calls))
        self.assertNotEqual(first.cache_key, second.cache_key)

    def test_calculator_uses_local_code_and_zero_model_calls(self) -> None:
        cheap = FakeExecutor()
        strong = FakeExecutor()
        result = self.router(cheap=cheap, strong=strong).route(
            RouterRequest("Calculate (9 * 7) - 4")
        )

        self.assertEqual("local_code", result.route)
        self.assertEqual("59", result.answer)
        self.assertEqual("calculator", result.recipe_name)
        self.assertEqual([], cheap.calls)
        self.assertEqual([], strong.calls)
        self.assertEqual(0, self.store.model_call_count())

    def test_inline_word_count_uses_local_code(self) -> None:
        result = self.router(
            cheap=FakeExecutor(),
            strong=FakeExecutor(),
        ).route(RouterRequest("Count words: Now is the time."))

        self.assertEqual("local_code", result.route)
        self.assertEqual("4 words", result.answer)
        self.assertEqual("inline-word-count", result.recipe_name)

    def test_cheap_route_preserves_user_text_and_sends_selected_source(self) -> None:
        source = self.root / "notes.md"
        source.write_text(
            ("Unrelated background.\n" * 300)
            + "Launch decision: Wednesday with a smaller scope.\n",
            encoding="utf-8",
        )
        cheap = FakeExecutor(answer="Launch Wednesday with a smaller scope.")
        request_text = "  Summarize the launch decision in one sentence.  "

        result = self.router(cheap=cheap, strong=FakeExecutor()).route(
            RouterRequest(request_text, sources=(source,))
        )

        self.assertEqual("cheap_model", result.route)
        self.assertEqual(1, len(cheap.calls))
        call = cheap.calls[0]
        self.assertEqual(request_text, call.user_text)
        self.assertIn(str(source.resolve()), call.selected_sources)
        self.assertIn("Wednesday with a smaller scope", call.packet_text)
        self.assertLess(len(call.packet_text), source.stat().st_size)

    def test_strong_route_gets_selected_source_without_calling_cheap_model(self) -> None:
        source = self.root / "research.md"
        source.write_text(
            "The observed result was a 42 percent reduction.\n",
            encoding="utf-8",
        )
        cheap = FakeExecutor()
        strong = FakeExecutor(answer="The evidence supports a narrow thesis.")

        result = self.router(cheap=cheap, strong=strong).route(
            RouterRequest(
                "Research and develop a defensible thesis from this evidence.",
                sources=(source,),
            )
        )

        self.assertEqual("strong_model", result.route)
        self.assertEqual([], cheap.calls)
        self.assertEqual(1, len(strong.calls))
        self.assertIn("42 percent reduction", strong.calls[0].packet_text)

    def test_external_action_stops_before_cache_recipe_or_model(self) -> None:
        cheap = FakeExecutor()
        strong = FakeExecutor()

        result = self.router(cheap=cheap, strong=strong).route(
            RouterRequest("Publish this post to the company account.")
        )

        self.assertEqual("stop", result.route)
        self.assertIn("change something outside Ringer", result.reason)
        self.assertEqual(0, result.model_calls)
        self.assertEqual([], cheap.calls)
        self.assertEqual([], strong.calls)

    def test_missing_supplied_source_stops_before_model(self) -> None:
        cheap = FakeExecutor()
        result = self.router(cheap=cheap, strong=FakeExecutor()).route(
            RouterRequest(
                "Summarize the source.",
                sources=(self.root / "missing.md",),
            )
        )

        self.assertEqual("stop", result.route)
        self.assertIn("no usable text", result.reason.lower())
        self.assertEqual([], cheap.calls)

    def test_fresh_input_preflight_limit_stops_before_model(self) -> None:
        cheap = FakeExecutor(overhead=100)
        limits = RouterLimits(
            max_fresh_input_tokens=50,
            max_reused_input_tokens=100,
            max_output_tokens=100,
            max_calls=1,
        )

        result = self.router(
            cheap=cheap,
            strong=FakeExecutor(),
            limits=limits,
        ).route(RouterRequest("Summarize this."))

        self.assertEqual("stop", result.route)
        self.assertIn("above the 50-token limit", result.reason)
        self.assertEqual([], cheap.calls)
        self.assertEqual(0, self.store.model_call_count())

    def test_zero_call_limit_stops_before_model(self) -> None:
        cheap = FakeExecutor()
        limits = RouterLimits(
            max_fresh_input_tokens=1_000,
            max_reused_input_tokens=100,
            max_output_tokens=100,
            max_calls=0,
        )

        result = self.router(
            cheap=cheap,
            strong=FakeExecutor(),
            limits=limits,
        ).route(RouterRequest("Summarize this."))

        self.assertEqual("stop", result.route)
        self.assertIn("call limit is zero", result.reason)
        self.assertEqual([], cheap.calls)

    def test_reused_input_over_limit_is_recorded_and_never_retried(self) -> None:
        cheap = FakeExecutor(
            usage=reported_usage(fresh=40, reused=101, output=10)
        )
        limits = RouterLimits(
            max_fresh_input_tokens=1_000,
            max_reused_input_tokens=100,
            max_output_tokens=100,
            max_calls=3,
        )
        router = self.router(
            cheap=cheap,
            strong=FakeExecutor(),
            limits=limits,
        )

        first = router.route(RouterRequest("Summarize this result."))

        self.assertEqual("stop", first.route)
        self.assertEqual(1, first.model_calls)
        self.assertEqual(1, len(cheap.calls))
        self.assertIn("Ringer did not retry", first.reason)
        self.assertEqual(0, self.store.accepted_count())
        call_row = self.store.model_calls()[0]
        self.assertEqual("budget_exceeded", call_row["status"])
        self.assertEqual(101, call_row["reused_input_tokens"])
        self.assertEqual(
            "provider_reported", call_row["reused_input_source"]
        )

    def test_output_over_limit_is_not_saved_as_an_accepted_result(self) -> None:
        cheap = FakeExecutor(
            answer="Long answer.",
            usage=reported_usage(output=51),
        )
        limits = RouterLimits(
            max_fresh_input_tokens=1_000,
            max_reused_input_tokens=100,
            max_output_tokens=50,
            max_calls=1,
        )
        router = self.router(
            cheap=cheap,
            strong=FakeExecutor(),
            limits=limits,
        )
        request = RouterRequest("Summarize this answer.")

        first = router.route(request)
        second = router.route(request)

        self.assertEqual("stop", first.route)
        self.assertEqual("stop", second.route)
        self.assertEqual(2, len(cheap.calls))
        self.assertEqual(0, self.store.accepted_count())

    def test_executor_failure_is_one_call_with_no_fallback(self) -> None:
        cheap = FakeExecutor(error=RuntimeError("network down"))
        strong = FakeExecutor()

        result = self.router(cheap=cheap, strong=strong).route(
            RouterRequest("Summarize this.")
        )

        self.assertEqual("stop", result.route)
        self.assertEqual(1, result.model_calls)
        self.assertEqual(1, len(cheap.calls))
        self.assertEqual([], strong.calls)
        self.assertIn("did not retry", result.reason)
        call_row = self.store.model_calls()[0]
        self.assertEqual("error", call_row["status"])
        self.assertEqual("local_estimate", call_row["fresh_input_source"])
        self.assertEqual("unavailable", call_row["output_source"])

    def test_every_usage_category_and_source_label_is_persisted(self) -> None:
        cheap = FakeExecutor(
            model_name="cheap/one",
            usage=reported_usage(
                fresh=11,
                reused=12,
                cache_write=13,
                output=14,
                reasoning=15,
            ),
        )
        result = self.router(cheap=cheap, strong=FakeExecutor()).route(
            RouterRequest("Summarize this.")
        )

        self.assertEqual("cheap_model", result.route)
        row = self.store.model_calls()[0]
        self.assertEqual("cheap_model", row["route"])
        self.assertEqual("cheap/one", row["model_name"])
        for prefix, value in (
            ("fresh_input", 11),
            ("reused_input", 12),
            ("cache_write_input", 13),
            ("output", 14),
            ("reasoning", 15),
        ):
            self.assertEqual(value, row[f"{prefix}_tokens"])
            self.assertEqual(
                "provider_reported", row[f"{prefix}_source"]
            )

    def test_unreported_reused_input_stops_because_limit_cannot_be_proved(self) -> None:
        cheap = FakeExecutor(
            reused_estimate=None,
            usage=ModelUsage(
                fresh_input=TokenValue.reported(20),
                output=TokenValue.reported(10),
            ),
        )

        result = self.router(cheap=cheap, strong=FakeExecutor()).route(
            RouterRequest("Summarize this.")
        )

        self.assertEqual("stop", result.route)
        self.assertIn("reused input was not reported", result.reason)
        row = self.store.model_calls()[0]
        self.assertEqual("unavailable", row["reused_input_source"])
        self.assertIsNone(row["reused_input_tokens"])

    def test_manual_acceptance_controls_exact_cache_when_auto_accept_is_off(self) -> None:
        cheap = FakeExecutor(answer="Accepted answer.")
        router = self.router(
            cheap=cheap,
            strong=FakeExecutor(),
            auto_accept=False,
        )
        request = RouterRequest("Summarize this.")

        first = router.route(request)
        second = router.route(request)
        router.accept_result(second)
        third = router.route(request)

        self.assertEqual("cheap_model", first.route)
        self.assertEqual("cheap_model", second.route)
        self.assertEqual("accepted_cache", third.route)
        self.assertEqual(2, len(cheap.calls))

    def test_accepted_cache_survives_router_restart(self) -> None:
        cheap = FakeExecutor(answer="Persistent answer.")
        request = RouterRequest("Summarize this.")
        first_router = self.router(cheap=cheap, strong=FakeExecutor())

        first = first_router.route(request)
        reopened_store = RouterStore(self.root / "router.sqlite3")
        second_cheap = FakeExecutor(answer="Should not run.")
        second_router = PreCallRouter(
            store=reopened_store,
            cheap_executor=second_cheap,
            strong_executor=FakeExecutor(),
        )
        second = second_router.route(request)

        self.assertEqual("cheap_model", first.route)
        self.assertEqual("accepted_cache", second.route)
        self.assertEqual([], second_cheap.calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
