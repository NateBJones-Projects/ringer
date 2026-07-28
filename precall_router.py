#!/usr/bin/env python3
"""A zero-planning, pre-model router for one normal-language request."""

from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import math
import operator
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

from context_packet import ContextPacket, build_context_packet


TOKEN_SOURCES = frozenset(
    {"provider_reported", "local_estimate", "unavailable"}
)
SUCCESS_ROUTES = frozenset(
    {"accepted_cache", "local_code", "cheap_model", "strong_model"}
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_tokens(text: str) -> int:
    """Return a conservative local estimate without calling a tokenizer model."""
    byte_count = len(text.encode("utf-8"))
    return math.ceil(byte_count / 4)


@dataclass(frozen=True)
class TokenValue:
    value: int | None
    source: str

    def __post_init__(self) -> None:
        if self.source not in TOKEN_SOURCES:
            raise ValueError(f"invalid token source: {self.source}")
        if self.source == "unavailable":
            if self.value is not None:
                raise ValueError("unavailable token values must be None")
            return
        if not isinstance(self.value, int) or self.value < 0:
            raise ValueError("reported and estimated token values must be nonnegative integers")

    @classmethod
    def reported(cls, value: int) -> "TokenValue":
        return cls(value=value, source="provider_reported")

    @classmethod
    def estimated(cls, value: int) -> "TokenValue":
        return cls(value=value, source="local_estimate")

    @classmethod
    def unavailable(cls) -> "TokenValue":
        return cls(value=None, source="unavailable")


@dataclass(frozen=True)
class ModelUsage:
    fresh_input: TokenValue = field(default_factory=TokenValue.unavailable)
    reused_input: TokenValue = field(default_factory=TokenValue.unavailable)
    cache_write_input: TokenValue = field(default_factory=TokenValue.unavailable)
    output: TokenValue = field(default_factory=TokenValue.unavailable)
    reasoning: TokenValue = field(default_factory=TokenValue.unavailable)


@dataclass(frozen=True)
class RouterLimits:
    max_fresh_input_tokens: int = 12_000
    max_reused_input_tokens: int = 5_000
    max_output_tokens: int = 4_000
    max_calls: int = 1

    def __post_init__(self) -> None:
        for name, value in (
            ("max_fresh_input_tokens", self.max_fresh_input_tokens),
            ("max_reused_input_tokens", self.max_reused_input_tokens),
            ("max_output_tokens", self.max_output_tokens),
            ("max_calls", self.max_calls),
        ):
            if not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")


@dataclass(frozen=True)
class RouterRequest:
    text: str
    sources: tuple[Path, ...] = ()
    state_files: tuple[Path, ...] = ()
    session_id: str = "default"
    max_packet_bytes: int = 16_000
    max_file_bytes: int = 4_000_000
    max_files: int = 200


@dataclass(frozen=True)
class ModelCall:
    route: str
    reason: str
    user_text: str
    packet_text: str
    selected_sources: tuple[str, ...]
    max_output_tokens: int
    max_fresh_input_tokens: int
    max_reused_input_tokens: int


@dataclass(frozen=True)
class ModelResponse:
    answer: str
    usage: ModelUsage = field(default_factory=ModelUsage)


class ModelExecutor(Protocol):
    model_name: str
    estimated_input_overhead_tokens: int
    estimated_reused_input_tokens: int

    def __call__(self, call: ModelCall) -> ModelResponse: ...


@dataclass(frozen=True)
class LocalRecipe:
    name: str
    matches: Callable[[RouterRequest, ContextPacket], bool]
    run: Callable[[RouterRequest, ContextPacket], str]


@dataclass(frozen=True)
class RouterResult:
    turn_id: str
    route: str
    reason: str
    answer: str | None
    model_calls: int
    cache_key: str
    usage: ModelUsage | None = None
    recipe_name: str | None = None

    @property
    def ok(self) -> bool:
        return self.route in SUCCESS_ROUTES and self.answer is not None


class RulePolicy:
    """Choose a route with fixed text rules; this class never calls a model."""

    _ACTION_REQUEST = re.compile(
        r"^\s*(?:please\s+)?(?:send|email|publish|post|upload|deploy|delete|"
        r"erase|buy|purchase|transfer|book|schedule)\b",
        re.IGNORECASE,
    )
    _STRONG_WORDS = re.compile(
        r"\b(?:architect|build|compare|critique|debug|design|diagnose|"
        r"implement|investigate|research|strategy|thesis|refactor|"
        r"fact[- ]check|legal|medical|financial)\b",
        re.IGNORECASE,
    )
    _CHEAP_OPENING = re.compile(
        r"^\s*(?:summarize|extract|classify|format|translate|proofread|"
        r"list|identify|name|when|where|who)\b",
        re.IGNORECASE,
    )

    def stop_reason(self, request: RouterRequest) -> str | None:
        if not request.text.strip():
            return "The request is empty, so Ringer stopped before any model call."
        if self._ACTION_REQUEST.search(request.text):
            return (
                "This request would change something outside Ringer. "
                "The read-only pre-call router stopped instead of spending a model call."
            )
        return None

    def model_route(
        self,
        request: RouterRequest,
        packet: ContextPacket,
    ) -> tuple[str, str]:
        selected_bytes = sum(
            len(chunk.text.encode("utf-8")) for chunk in packet.selected
        )
        if (
            self._STRONG_WORDS.search(request.text)
            or len(request.text) > 500
            or selected_bytes > 12_000
        ):
            return (
                "strong_model",
                "The request needs substantial judgment, so a clean strong model gets only the selected text.",
            )
        if self._CHEAP_OPENING.search(request.text) or len(request.text) <= 240:
            return (
                "cheap_model",
                "The request is short and bounded, so a cheaper model gets only the selected text.",
            )
        return (
            "strong_model",
            "The request is not safely covered by a cheap rule, so a clean strong model gets only the selected text.",
        )


class RouterStore:
    """Persistent accepted answers, turn outcomes, and per-call token use."""

    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        return connection

    @contextlib.contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS accepted_results (
                    cache_key TEXT PRIMARY KEY,
                    answer TEXT NOT NULL,
                    origin_route TEXT NOT NULL,
                    origin_reason TEXT NOT NULL,
                    accepted_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS router_turns (
                    turn_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    cache_key TEXT NOT NULL,
                    route TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    status TEXT NOT NULL,
                    model_calls INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    turn_id TEXT NOT NULL,
                    route TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    fresh_input_tokens INTEGER,
                    fresh_input_source TEXT NOT NULL,
                    reused_input_tokens INTEGER,
                    reused_input_source TEXT NOT NULL,
                    cache_write_input_tokens INTEGER,
                    cache_write_input_source TEXT NOT NULL,
                    output_tokens INTEGER,
                    output_source TEXT NOT NULL,
                    reasoning_tokens INTEGER,
                    reasoning_source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS model_calls_turn_id
                    ON model_calls(turn_id);
                """
            )

    def accepted_answer(self, cache_key: str) -> sqlite3.Row | None:
        with self._connection() as connection:
            return connection.execute(
                """
                SELECT cache_key, answer, origin_route, origin_reason, accepted_at
                FROM accepted_results
                WHERE cache_key = ?
                """,
                (cache_key,),
            ).fetchone()

    def accept(
        self,
        *,
        cache_key: str,
        answer: str,
        origin_route: str,
        origin_reason: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO accepted_results (
                    cache_key, answer, origin_route, origin_reason, accepted_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    answer = excluded.answer,
                    origin_route = excluded.origin_route,
                    origin_reason = excluded.origin_reason,
                    accepted_at = excluded.accepted_at
                """,
                (cache_key, answer, origin_route, origin_reason, utc_now()),
            )

    @staticmethod
    def _usage_values(usage: ModelUsage) -> tuple[object, ...]:
        return (
            usage.fresh_input.value,
            usage.fresh_input.source,
            usage.reused_input.value,
            usage.reused_input.source,
            usage.cache_write_input.value,
            usage.cache_write_input.source,
            usage.output.value,
            usage.output.source,
            usage.reasoning.value,
            usage.reasoning.source,
        )

    def start_model_call(
        self,
        *,
        turn_id: str,
        route: str,
        reason: str,
        model_name: str,
        usage: ModelUsage,
    ) -> int:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO model_calls (
                    turn_id, route, reason, model_name, status,
                    fresh_input_tokens, fresh_input_source,
                    reused_input_tokens, reused_input_source,
                    cache_write_input_tokens, cache_write_input_source,
                    output_tokens, output_source,
                    reasoning_tokens, reasoning_source,
                    started_at, completed_at
                ) VALUES (
                    ?, ?, ?, ?, 'started',
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL
                )
                """,
                (
                    turn_id,
                    route,
                    reason,
                    model_name,
                    *self._usage_values(usage),
                    utc_now(),
                ),
            )
            return int(cursor.lastrowid)

    def finish_model_call(
        self,
        call_id: int,
        *,
        status: str,
        usage: ModelUsage,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                UPDATE model_calls SET
                    status = ?,
                    fresh_input_tokens = ?, fresh_input_source = ?,
                    reused_input_tokens = ?, reused_input_source = ?,
                    cache_write_input_tokens = ?, cache_write_input_source = ?,
                    output_tokens = ?, output_source = ?,
                    reasoning_tokens = ?, reasoning_source = ?,
                    completed_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    *self._usage_values(usage),
                    utc_now(),
                    call_id,
                ),
            )

    def record_turn(
        self,
        result: RouterResult,
        *,
        request_hash: str,
        status: str,
    ) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO router_turns (
                    turn_id, request_hash, cache_key, route, reason,
                    status, model_calls, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.turn_id,
                    request_hash,
                    result.cache_key,
                    result.route,
                    result.reason,
                    status,
                    result.model_calls,
                    utc_now(),
                ),
            )

    def model_call_count(self, turn_id: str | None = None) -> int:
        query = "SELECT COUNT(*) FROM model_calls"
        values: tuple[str, ...] = ()
        if turn_id is not None:
            query += " WHERE turn_id = ?"
            values = (turn_id,)
        with self._connection() as connection:
            return int(connection.execute(query, values).fetchone()[0])

    def accepted_count(self) -> int:
        with self._connection() as connection:
            return int(
                connection.execute("SELECT COUNT(*) FROM accepted_results").fetchone()[0]
            )

    def model_calls(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM model_calls ORDER BY id"
            ).fetchall()
        return [dict(row) for row in rows]

    def turns(self) -> list[dict[str, object]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM router_turns ORDER BY created_at, turn_id"
            ).fetchall()
        return [dict(row) for row in rows]


_BINARY_OPERATORS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_CALCULATOR_REQUEST = re.compile(
    r"^\s*(?:calculate|compute|what\s+is)\s+(.+?)\s*\??\s*$",
    re.IGNORECASE,
)
_INLINE_WORD_COUNT = re.compile(
    r"^\s*(?:count\s+(?:the\s+)?words(?:\s+in)?|"
    r"how\s+many\s+words(?:\s+are\s+in)?)\s*:\s*(.+)\s*$",
    re.IGNORECASE | re.DOTALL,
)


def _calculator_expression(text: str) -> str | None:
    match = _CALCULATOR_REQUEST.fullmatch(text)
    if match is None:
        return None
    expression = match.group(1).strip()
    if not expression or len(expression) > 200:
        return None
    if re.fullmatch(r"[0-9eE+\-*/%().\s]+", expression) is None:
        return None
    try:
        _evaluate_arithmetic(ast.parse(expression, mode="eval").body)
    except (SyntaxError, TypeError, ValueError, ZeroDivisionError, OverflowError):
        return None
    return expression


def _evaluate_arithmetic(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        value = float(node.value)
    elif isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate_arithmetic(node.left)
        right = _evaluate_arithmetic(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise ValueError("exponent is too large")
        value = _BINARY_OPERATORS[type(node.op)](left, right)
    elif isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        value = _UNARY_OPERATORS[type(node.op)](_evaluate_arithmetic(node.operand))
    else:
        raise ValueError("unsupported arithmetic expression")
    if not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError("arithmetic result is too large")
    return value


def _calculator_matches(request: RouterRequest, _packet: ContextPacket) -> bool:
    return _calculator_expression(request.text) is not None


def _calculator_run(request: RouterRequest, _packet: ContextPacket) -> str:
    expression = _calculator_expression(request.text)
    if expression is None:
        raise ValueError("calculator recipe did not receive a valid expression")
    value = _evaluate_arithmetic(ast.parse(expression, mode="eval").body)
    if value.is_integer():
        return str(int(value))
    return format(value, ".12g")


def _word_count_matches(request: RouterRequest, _packet: ContextPacket) -> bool:
    return _INLINE_WORD_COUNT.fullmatch(request.text) is not None


def _word_count_run(request: RouterRequest, _packet: ContextPacket) -> str:
    match = _INLINE_WORD_COUNT.fullmatch(request.text)
    if match is None:
        raise ValueError("word-count recipe did not receive inline text")
    words = re.findall(r"\b[\w]+(?:[’'-][\w]+)*\b", match.group(1), re.UNICODE)
    label = "word" if len(words) == 1 else "words"
    return f"{len(words)} {label}"


def default_recipes() -> tuple[LocalRecipe, ...]:
    return (
        LocalRecipe(
            name="calculator",
            matches=_calculator_matches,
            run=_calculator_run,
        ),
        LocalRecipe(
            name="inline-word-count",
            matches=_word_count_matches,
            run=_word_count_run,
        ),
    )


def _request_hash(request: RouterRequest) -> str:
    return hashlib.sha256(request.text.encode("utf-8")).hexdigest()


def _cache_key(request: RouterRequest, packet: ContextPacket | None) -> str:
    material = {
        "request_exact": request.text,
        "packet": packet.text if packet is not None else "",
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _executor_estimate(executor: ModelExecutor, name: str) -> int | None:
    value = getattr(executor, name, None)
    if value is None:
        return None
    if not isinstance(value, int) or value < 0:
        raise ValueError(f"executor {name} must be a nonnegative integer")
    return value


def _completed_usage(
    response: ModelResponse,
    *,
    starting_usage: ModelUsage,
) -> ModelUsage:
    usage = response.usage
    return ModelUsage(
        fresh_input=(
            usage.fresh_input
            if usage.fresh_input.source != "unavailable"
            else starting_usage.fresh_input
        ),
        reused_input=(
            usage.reused_input
            if usage.reused_input.source != "unavailable"
            else starting_usage.reused_input
        ),
        cache_write_input=usage.cache_write_input,
        output=(
            usage.output
            if usage.output.source != "unavailable"
            else TokenValue.estimated(estimate_tokens(response.answer))
        ),
        reasoning=usage.reasoning,
    )


def _budget_problem(usage: ModelUsage, limits: RouterLimits) -> str | None:
    checks = (
        (
            "fresh input",
            usage.fresh_input,
            limits.max_fresh_input_tokens,
        ),
        (
            "reused input",
            usage.reused_input,
            limits.max_reused_input_tokens,
        ),
        (
            "output",
            usage.output,
            limits.max_output_tokens,
        ),
    )
    for label, measured, limit in checks:
        if measured.source == "unavailable":
            return (
                f"{label} was not reported, so Ringer could not prove that it "
                f"stayed under the {limit:,}-token limit."
            )
        assert measured.value is not None
        if measured.value > limit:
            return (
                f"{label} used {measured.value:,} tokens, above the "
                f"{limit:,}-token limit."
            )
    return None


class PreCallRouter:
    """Route before the first expensive call, and never retry a failed call."""

    def __init__(
        self,
        *,
        store: RouterStore,
        cheap_executor: ModelExecutor | None,
        strong_executor: ModelExecutor | None,
        limits: RouterLimits | None = None,
        policy: RulePolicy | None = None,
        recipes: tuple[LocalRecipe, ...] | None = None,
        auto_accept: bool = False,
    ) -> None:
        self.store = store
        self.cheap_executor = cheap_executor
        self.strong_executor = strong_executor
        self.limits = limits or RouterLimits()
        self.policy = policy or RulePolicy()
        self.recipes = default_recipes() if recipes is None else recipes
        self.auto_accept = auto_accept

    def _record(
        self,
        result: RouterResult,
        *,
        request_hash: str,
        status: str,
    ) -> RouterResult:
        self.store.record_turn(
            result,
            request_hash=request_hash,
            status=status,
        )
        return result

    def _stop(
        self,
        *,
        turn_id: str,
        request_hash: str,
        cache_key: str,
        reason: str,
        model_calls: int = 0,
        usage: ModelUsage | None = None,
    ) -> RouterResult:
        return self._record(
            RouterResult(
                turn_id=turn_id,
                route="stop",
                reason=reason,
                answer=None,
                model_calls=model_calls,
                cache_key=cache_key,
                usage=usage,
            ),
            request_hash=request_hash,
            status="stopped",
        )

    def accept_result(self, result: RouterResult) -> None:
        if not result.ok or result.answer is None:
            raise ValueError("only a successful answer can be accepted")
        self.store.accept(
            cache_key=result.cache_key,
            answer=result.answer,
            origin_route=result.route,
            origin_reason=result.reason,
        )

    def route(self, request: RouterRequest) -> RouterResult:
        turn_id = str(uuid.uuid4())
        request_hash = _request_hash(request)
        empty_cache_key = _cache_key(request, None)
        stop_reason = self.policy.stop_reason(request)
        if stop_reason is not None:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=empty_cache_key,
                reason=stop_reason,
            )

        try:
            packet = build_context_packet(
                request.text,
                sources=request.sources,
                state_files=request.state_files,
                max_packet_bytes=request.max_packet_bytes,
                max_file_bytes=request.max_file_bytes,
                max_files=request.max_files,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=empty_cache_key,
                reason=f"Ringer could not build a safe source packet: {exc}",
            )

        cache_key = _cache_key(request, packet)
        if (request.sources or request.state_files) and not packet.selected:
            detail = "; ".join(packet.skipped) or "no source passage matched"
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=(
                    "Ringer found no usable text in the supplied sources, "
                    f"so it stopped before a model call: {detail}"
                ),
            )

        accepted = self.store.accepted_answer(cache_key)
        if accepted is not None:
            return self._record(
                RouterResult(
                    turn_id=turn_id,
                    route="accepted_cache",
                    reason=(
                        "The exact request and selected text already have an accepted answer."
                    ),
                    answer=str(accepted["answer"]),
                    model_calls=0,
                    cache_key=cache_key,
                ),
                request_hash=request_hash,
                status="success",
            )

        for recipe in self.recipes:
            try:
                matched = recipe.matches(request, packet)
            except Exception as exc:
                return self._stop(
                    turn_id=turn_id,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    reason=(
                        f"The local {recipe.name} recipe failed while checking the request "
                        f"({type(exc).__name__}); no model call was made."
                    ),
                )
            if not matched:
                continue
            try:
                answer = recipe.run(request, packet).strip()
            except Exception as exc:
                return self._stop(
                    turn_id=turn_id,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    reason=(
                        f"The local {recipe.name} recipe failed ({type(exc).__name__}); "
                        "Ringer did not fall through to a paid model."
                    ),
                )
            if not answer:
                return self._stop(
                    turn_id=turn_id,
                    request_hash=request_hash,
                    cache_key=cache_key,
                    reason=(
                        f"The local {recipe.name} recipe returned no answer; "
                        "Ringer did not fall through to a paid model."
                    ),
                )
            result = RouterResult(
                turn_id=turn_id,
                route="local_code",
                reason=(
                    f"Local code can answer this exactly with the {recipe.name} recipe; "
                    "no model call is needed."
                ),
                answer=answer,
                model_calls=0,
                cache_key=cache_key,
                recipe_name=recipe.name,
            )
            if self.auto_accept:
                self.accept_result(result)
            return self._record(
                result,
                request_hash=request_hash,
                status="success",
            )

        model_route, model_reason = self.policy.model_route(request, packet)
        executor = (
            self.cheap_executor
            if model_route == "cheap_model"
            else self.strong_executor
        )
        if executor is None:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=f"No {model_route.replace('_', ' ')} is configured.",
            )
        if self.limits.max_calls < 1:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason="The call limit is zero, so Ringer stopped before any model call.",
            )
        if self.limits.max_output_tokens < 1:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason="The output limit is zero, so Ringer stopped before any model call.",
            )

        try:
            overhead = _executor_estimate(
                executor, "estimated_input_overhead_tokens"
            )
            reused_estimate = _executor_estimate(
                executor, "estimated_reused_input_tokens"
            )
        except ValueError as exc:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=f"Ringer rejected an invalid model estimate: {exc}",
            )
        fresh_estimate = estimate_tokens(packet.text) + (overhead or 0)
        starting_usage = ModelUsage(
            fresh_input=TokenValue.estimated(fresh_estimate),
            reused_input=(
                TokenValue.estimated(reused_estimate)
                if reused_estimate is not None
                else TokenValue.unavailable()
            ),
        )
        if fresh_estimate > self.limits.max_fresh_input_tokens:
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=(
                    f"The clean request is estimated at {fresh_estimate:,} fresh input "
                    f"tokens, above the {self.limits.max_fresh_input_tokens:,}-token limit."
                ),
            )
        if (
            reused_estimate is not None
            and reused_estimate > self.limits.max_reused_input_tokens
        ):
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=(
                    f"The model is estimated to reuse {reused_estimate:,} input tokens, "
                    f"above the {self.limits.max_reused_input_tokens:,}-token limit."
                ),
            )

        call = ModelCall(
            route=model_route,
            reason=model_reason,
            user_text=request.text,
            packet_text=packet.text,
            selected_sources=tuple(
                dict.fromkeys(chunk.path for chunk in packet.selected)
            ),
            max_output_tokens=self.limits.max_output_tokens,
            max_fresh_input_tokens=self.limits.max_fresh_input_tokens,
            max_reused_input_tokens=self.limits.max_reused_input_tokens,
        )
        model_name = str(getattr(executor, "model_name", model_route))
        call_id = self.store.start_model_call(
            turn_id=turn_id,
            route=model_route,
            reason=model_reason,
            model_name=model_name,
            usage=starting_usage,
        )
        try:
            response = executor(call)
            if not isinstance(response, ModelResponse):
                raise TypeError("executor must return ModelResponse")
        except Exception as exc:
            self.store.finish_model_call(
                call_id,
                status="error",
                usage=starting_usage,
            )
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=(
                    f"The {model_route.replace('_', ' ')} failed "
                    f"({type(exc).__name__}). Ringer did not retry it."
                ),
                model_calls=1,
                usage=starting_usage,
            )

        completed_usage = _completed_usage(
            response,
            starting_usage=starting_usage,
        )
        budget_problem = _budget_problem(completed_usage, self.limits)
        if budget_problem is not None:
            self.store.finish_model_call(
                call_id,
                status="budget_exceeded",
                usage=completed_usage,
            )
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=f"{budget_problem} Ringer did not retry the call.",
                model_calls=1,
                usage=completed_usage,
            )
        answer = response.answer.strip()
        if not answer:
            self.store.finish_model_call(
                call_id,
                status="error",
                usage=completed_usage,
            )
            return self._stop(
                turn_id=turn_id,
                request_hash=request_hash,
                cache_key=cache_key,
                reason=(
                    f"The {model_route.replace('_', ' ')} returned no answer. "
                    "Ringer did not retry the call."
                ),
                model_calls=1,
                usage=completed_usage,
            )

        self.store.finish_model_call(
            call_id,
            status="success",
            usage=completed_usage,
        )
        result = RouterResult(
            turn_id=turn_id,
            route=model_route,
            reason=model_reason,
            answer=answer,
            model_calls=1,
            cache_key=cache_key,
            usage=completed_usage,
        )
        if self.auto_accept:
            self.accept_result(result)
        return self._record(
            result,
            request_hash=request_hash,
            status="success",
        )
