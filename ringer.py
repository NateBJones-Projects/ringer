#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import re
import shlex
import signal
import shutil
import socket
import subprocess
import sys

try:
    import sqlite3
except Exception:  # pragma: no cover - exercised by monkeypatch in tests.
    sqlite3 = None  # type: ignore[assignment]

if sys.version_info < (3, 11):
    raise SystemExit(
        f"ringer requires Python 3.11+ (tomllib); found {sys.version.split()[0]} at {sys.executable}"
    )

import tempfile
import threading
import time
import tomllib
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field, replace as dataclass_replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html import escape as html_escape
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable


TOOL_NAME = "ringer"
STATE_DIR_NAME = ".ringer"
ENV_VAR_PREFIX = "RINGER"

CONFIG_DIR_NAME = TOOL_NAME
CONFIG_FILE_NAME = "config.toml"
DEFAULT_ENGINE_NAME = "codex"
DEFAULT_TIMEOUT_S = 900
CHECK_TIMEOUT_S = 60
DEFAULT_DASHBOARD_PORT_BASE = 8787
DEFAULT_HUD_PORT = 8700
DEFAULT_CATALOG_SOURCE = "https://openrouter.ai/api/v1/models"
CATALOG_AUTO_REFRESH_MAX_AGE_S = 24 * 60 * 60
CATALOG_FETCH_TIMEOUT_S = 5
DEFAULT_TOKEN_REGEX = r"tokens\s+used\s*:?\s*([0-9][0-9,]*)"
DEFAULT_CODEX_MODEL_REPORT_REGEX = r"(?m)^model:[ \t]*([^ \t\r\n]+)[ \t]*\r?$"
ACTIVITY_TAIL_BYTES = 2048
ACTIVITY_TEXT_LIMIT = 80
ARTIFACT_WRAPPER_TAIL_BYTES = 256 * 1024
ARTIFACT_LIBRARY_MAX_VERSIONS = 20
DELIVERABLE_MAX_BYTES = 20 * 1024 * 1024
WORKER_LOG_TAIL_BYTES = 64 * 1024
TASK_REPORT_FILENAMES = ("report.md", "report.html")
TEXT_DELIVERABLE_SUFFIXES = {".md", ".txt", ".log"}
IMAGE_DELIVERABLE_SUFFIXES = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".svg", ".webp"}
# When a task declares no expect_files, these are the file types worth
# rescuing from the top of its task directory so the work still shows up
# on the results page instead of silently staying invisible. Logs are
# excluded — the worker log is linked separately, it is not a deliverable.
FALLBACK_HARVEST_SUFFIXES = (
    (TEXT_DELIVERABLE_SUFFIXES - {".log"})
    | IMAGE_DELIVERABLE_SUFFIXES
    | {".html", ".htm", ".json", ".csv", ".pdf", ".mp4", ".webm", ".mov", ".gif"}
)
FALLBACK_HARVEST_MAX_FILES = 8
SHEPHERD_MODEL = f"none ({TOOL_NAME}.py)"
VERIFY_METHOD = "executed-check"
RESERVED_FIXTURE_MODELS = frozenset(
    {"proven-model", "probation-model", "mock-model", "test-model"}
)
UNATTRIBUTED_MODEL_DISPLAY = "(unattributed legacy rows)"
CSP_META_TAG = (
    '<meta http-equiv="Content-Security-Policy" '
    'content="default-src \'none\'; style-src \'unsafe-inline\'; img-src data:">'
)
DASHBOARD_HTML_PATH = Path(__file__).resolve().parent / "dashboard" / "dashboard.html"
RINGSIDE_HTML_PATH = Path(__file__).resolve().parent / "dashboard" / "ringside.html"
MINIMAL_DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>ringer dashboard</title></head>
<body style="font-family: system-ui, sans-serif; background:#080a0f; color:#eef4ff;">
<main id="app">dashboard/dashboard.html is missing</main>
<script>
function update(states) {
  document.getElementById("app").textContent = JSON.stringify(states, null, 2);
}
</script>
</body>
</html>
"""


@dataclass(frozen=True)
class EngineConfig:
    name: str
    bin: str
    args_template: tuple[str, ...]
    full_access_args: tuple[str, ...]
    sandbox_args: tuple[str, ...]
    token_regex: str | None = DEFAULT_TOKEN_REGEX
    model_report_regex: str | None = None
    # Fills the {model} placeholder in args_template when a task does not set
    # its own "model" — this is what makes a harness engine (OpenCode) model
    # agnostic instead of hard-coding one model into the command line.
    model_default: str = ""

    @property
    def process_name(self) -> str:
        return Path(self.bin).name or self.name


@dataclass(frozen=True)
class PostgresEvalConfig:
    env_file: Path


@dataclass(frozen=True)
class EvalConfig:
    backend: str
    jsonl_path: Path
    postgres: PostgresEvalConfig | None = None


@dataclass(frozen=True)
class ArtifactConfig:
    """Tier 0 zero-LLM HTML artifacts: live status page + final report + multi-run index.

    See ringer-live-artifacts-plan.md. Templates support {run_id}, {run_name} substitutions.
    """

    enabled: bool
    out_template: str
    report_template: str
    index_out: Path

    def artifact_path(self, run_id: str, run_name: str) -> Path:
        return Path(format_artifact_template(self.out_template, run_id, run_name))

    def report_path(self, run_id: str, run_name: str) -> Path:
        return Path(format_artifact_template(self.report_template, run_id, run_name))


@dataclass(frozen=True)
class SteeringConfig:
    dir: Path | None = None
    inject_candidates: bool = True


@dataclass(frozen=True)
class SteeringRule:
    id: str
    status: str
    audience: str
    inject: str


@dataclass(frozen=True)
class SteeringProfile:
    model: str
    profile_version: str
    slug: str
    rules: tuple[SteeringRule, ...]


STEERING_STATUSES = {"candidate", "confirmed", "refuted", "stale-pending-reverify"}
STEERING_AUDIENCES = {"driver", "worker"}
STEERING_RULE_HEADING_RE = re.compile(r"^## R\d+ · ([^\n]+?)\s*$", re.MULTILINE)


def load_steering_config(raw: Any) -> SteeringConfig:
    """Load optional steering settings without allowing them to break config load."""
    try:
        section = raw if isinstance(raw, dict) else {}
        env_dir = os.environ.get(f"{ENV_VAR_PREFIX}_STEERING_DIR")
        directory = optional_path(env_dir) if env_dir and env_dir.strip() else optional_path(
            section.get("dir")
        )
        return SteeringConfig(
            dir=directory,
            inject_candidates=bool(section.get("inject_candidates", True)),
        )
    except Exception:
        return SteeringConfig()


def _steering_yaml_values(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if line != line.lstrip():
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*?)\s*$", line)
        if not match:
            continue
        value = match.group(2).strip()
        value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[match.group(1)] = value
    return values


def parse_steering_profile(text: str, *, slug: str | None = None) -> SteeringProfile | None:
    """Parse the small, documented subset of steering profile markdown. Never raise."""
    try:
        lines = text.splitlines()
        if not lines or lines[0].strip() != "---":
            return None
        frontmatter_end = next(
            index for index in range(1, len(lines)) if lines[index].strip() == "---"
        )
        frontmatter = _steering_yaml_values("\n".join(lines[1:frontmatter_end]))
        model = frontmatter.get("model", "").strip()
        profile_version = frontmatter.get("profile_version", "").strip()
        if not model or not profile_version:
            return None

        body = "\n".join(lines[frontmatter_end + 1 :])
        headings = list(STEERING_RULE_HEADING_RE.finditer(body))
        rules: list[SteeringRule] = []
        for heading in headings:
            next_section = re.search(r"^## ", body[heading.end() :], re.MULTILINE)
            section_end = (
                heading.end() + next_section.start() if next_section is not None else len(body)
            )
            section = body[heading.end() : section_end]
            yaml_match = re.search(r"```yaml[ \t]*\n(.*?)^```[ \t]*$", section, re.MULTILINE | re.DOTALL)
            if yaml_match is None:
                continue
            metadata = _steering_yaml_values(yaml_match.group(1))
            rule_id = metadata.get("id", "").strip()
            status = metadata.get("status", "").strip().lower()
            audience = metadata.get("audience", "driver").strip().lower() or "driver"
            if not rule_id or status not in STEERING_STATUSES or audience not in STEERING_AUDIENCES:
                continue

            inject_parts: list[str] = []
            found_inject = False
            for line in section.splitlines():
                stripped = line.strip()
                if not found_inject:
                    if stripped.startswith("**Inject:**"):
                        found_inject = True
                        first = stripped.removeprefix("**Inject:**").strip()
                        if first:
                            inject_parts.append(first)
                    continue
                if not stripped:
                    break
                inject_parts.append(stripped)
            inject_text = " ".join(inject_parts).strip()
            if not inject_text:
                continue
            rules.append(
                SteeringRule(
                    id=rule_id,
                    status=status,
                    audience=audience,
                    inject=inject_text,
                )
            )
        if not rules:
            return None
        return SteeringProfile(
            model=model,
            profile_version=profile_version,
            slug=(slug or model).strip(),
            rules=tuple(rules),
        )
    except Exception:
        return None


def load_steering_profile(path: Path) -> SteeringProfile | None:
    try:
        return parse_steering_profile(
            path.read_text(encoding="utf-8", errors="replace"),
            slug=path.stem,
        )
    except Exception:
        return None


def steering_profile_candidates(steering_dir: Path, resolved_model: str) -> tuple[Path, ...]:
    model = resolved_model.strip().lower()
    if not model:
        return ()
    slugs = [model.replace("/", "-"), model.rsplit("/", 1)[-1]]
    unique_slugs = tuple(dict.fromkeys(slugs))
    return tuple(steering_dir / "profiles" / f"{slug}.md" for slug in unique_slugs)


def resolve_steering_profile(
    steering_dir: Path | None, resolved_model: str
) -> SteeringProfile | None:
    """Return the first matching profile path, even when that file is malformed."""
    try:
        if steering_dir is None:
            return None
        for candidate in steering_profile_candidates(steering_dir, resolved_model):
            if candidate.is_file():
                return load_steering_profile(candidate)
    except Exception:
        return None
    return None


def steering_worker_rules(
    profile: SteeringProfile, *, inject_candidates: bool = True
) -> tuple[SteeringRule, ...]:
    try:
        return tuple(
            rule
            for rule in profile.rules
            if rule.audience == "worker"
            and (
                rule.status in {"confirmed", "stale-pending-reverify"}
                or (rule.status == "candidate" and inject_candidates)
            )
        )
    except Exception:
        return ()


def inject_steering_spec(
    spec: str,
    profile: SteeringProfile | None,
    *,
    inject_candidates: bool = True,
) -> tuple[str, tuple[str, ...]]:
    """Prepend qualifying worker rules, returning the original spec on any error."""
    try:
        if profile is None:
            return spec, ()
        rules = steering_worker_rules(profile, inject_candidates=inject_candidates)
        if not rules:
            return spec, ()
        lines = [
            f"[Steering profile {profile.model} v{profile.profile_version} — auto-injected by ringer.py]"
        ]
        for rule in rules:
            prefix = ""
            if rule.status == "candidate":
                prefix = "(candidate) "
            elif rule.status == "stale-pending-reverify":
                prefix = "(unverified on current model version) "
            lines.append(f"- {prefix}{rule.inject}")
        lines.append("[End steering profile]")
        return "\n".join(lines) + "\n\n" + spec, tuple(rule.id for rule in rules)
    except Exception:
        return spec, ()


def format_artifact_template(template: str, run_id: str, run_name: str) -> str:
    text = template.replace("{run_id}", run_id).replace("{run_name}", run_name)
    return str(Path(text).expanduser())


def load_artifact_config(raw: Any, state_dir: Path) -> ArtifactConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("artifact must be a TOML table")
    default_dir = state_dir / "artifacts"
    enabled = bool(raw.get("enabled", True))
    out_template = str(raw.get("out", str(default_dir / "{run_id}.html")))
    report_template = str(raw.get("report_out", str(default_dir / "{run_id}-report.html")))
    index_out = expand_path(raw.get("index_out"), default_dir / "index.html")
    return ArtifactConfig(
        enabled=enabled,
        out_template=out_template,
        report_template=report_template,
        index_out=index_out,
    )


@dataclass(frozen=True)
class AppConfig:
    path: Path | None
    identity_default: str | None
    state_dir: Path
    dashboard_port_base: int
    hud_port: int
    hud_app_path: Path | None
    allow_full_access: bool
    eval: EvalConfig
    engines: dict[str, EngineConfig]
    artifact: ArtifactConfig
    steering: SteeringConfig = field(default_factory=SteeringConfig)

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        config_path = path or env_config_path() or default_config_path()
        explicit = path is not None or env_config_path() is not None
        data: dict[str, Any] = {}
        if config_path.exists():
            with config_path.open("rb") as fh:
                loaded = tomllib.load(fh)
            if not isinstance(loaded, dict):
                raise ValueError("config root must be a TOML table")
            data = loaded
        elif explicit:
            raise ValueError(f"config file not found: {config_path}")

        state_dir = expand_path(data.get("state_dir"), default_state_dir())
        dashboard_port_base = int(data.get("dashboard_port_base", DEFAULT_DASHBOARD_PORT_BASE))
        if dashboard_port_base <= 0:
            raise ValueError("dashboard_port_base must be positive")
        hud_port = load_hud_port(data.get("hud"))
        identity_default = optional_string(data.get("identity_default"))
        hud_app_path = optional_path(data.get("hud_app_path"))
        allow_full_access = bool(data.get("allow_full_access", False))
        eval_config = load_eval_config(data.get("eval"), state_dir)
        engines = load_engines(data.get("engines"))
        artifact_config = load_artifact_config(data.get("artifact"), state_dir)
        try:
            steering_config = load_steering_config(data.get("steering"))
        except Exception:
            # Steering is optional and must never make the base config unusable,
            # including when its loader itself is replaced or extended later.
            steering_config = SteeringConfig()
        return cls(
            path=config_path if config_path.exists() else None,
            identity_default=identity_default,
            state_dir=state_dir,
            dashboard_port_base=dashboard_port_base,
            hud_port=hud_port,
            hud_app_path=hud_app_path,
            allow_full_access=allow_full_access,
            eval=eval_config,
            engines=engines,
            artifact=artifact_config,
            steering=steering_config,
        )


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    config_home = Path(base).expanduser() if base else Path.home() / ".config"
    return config_home / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def env_config_path() -> Path | None:
    value = os.environ.get(f"{ENV_VAR_PREFIX}_CONFIG")
    if not value or not value.strip():
        return None
    return Path(value).expanduser().resolve()


def default_state_dir() -> Path:
    return Path.home() / STATE_DIR_NAME


def expand_path(value: Any, default: Path) -> Path:
    if value is None:
        return default.expanduser().resolve()
    return Path(str(value)).expanduser().resolve()


def optional_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(text).expanduser().resolve()


def optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_string_tuple(value: Any, *, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a list")
    return tuple(str(item) for item in value)


def built_in_codex_engine() -> EngineConfig:
    resolved = shutil.which(DEFAULT_ENGINE_NAME) or DEFAULT_ENGINE_NAME
    return EngineConfig(
        name=DEFAULT_ENGINE_NAME,
        bin=resolved,
        args_template=(
            "exec",
            "--skip-git-repo-check",
            "{access_args}",
            "{model_args}",
            "{engine_args}",
            "-C",
            "{taskdir}",
            "{spec}",
        ),
        full_access_args=("--dangerously-bypass-approvals-and-sandbox",),
        sandbox_args=("--sandbox", "workspace-write"),
        token_regex=DEFAULT_TOKEN_REGEX,
        model_report_regex=DEFAULT_CODEX_MODEL_REPORT_REGEX,
    )


def load_eval_config(raw: Any, state_dir: Path) -> EvalConfig:
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("eval must be a TOML table")
    backend = str(raw.get("backend", "jsonl")).strip().lower()
    if backend not in {"jsonl", "postgres"}:
        raise ValueError("eval.backend must be 'jsonl' or 'postgres'")
    jsonl_path = expand_path(raw.get("jsonl_path"), state_dir / "runs.jsonl")
    postgres: PostgresEvalConfig | None = None
    postgres_raw = raw.get("postgres")
    if postgres_raw is not None:
        if not isinstance(postgres_raw, dict):
            raise ValueError("eval.postgres must be a TOML table")
        env_file_raw = optional_string(postgres_raw.get("env_file"))
        if env_file_raw is None:
            raise ValueError("eval.postgres.env_file is required")
        env_file = Path(env_file_raw).expanduser().resolve()
        postgres = PostgresEvalConfig(env_file=env_file)
    if backend == "postgres" and postgres is None:
        raise ValueError("eval.backend='postgres' requires [eval.postgres].env_file")
    return EvalConfig(backend=backend, jsonl_path=jsonl_path, postgres=postgres)


def load_hud_port(raw: Any) -> int:
    if raw is None:
        return DEFAULT_HUD_PORT
    if not isinstance(raw, dict):
        raise ValueError("hud must be a TOML table")
    port = int(raw.get("port", DEFAULT_HUD_PORT))
    if port <= 0:
        raise ValueError("hud.port must be positive")
    return port


def load_engines(raw: Any) -> dict[str, EngineConfig]:
    engines: dict[str, EngineConfig] = {DEFAULT_ENGINE_NAME: built_in_codex_engine()}
    if raw is None:
        return engines
    if not isinstance(raw, dict):
        raise ValueError("engines must be a TOML table")
    for name, section in raw.items():
        if not isinstance(section, dict):
            raise ValueError(f"engines.{name} must be a TOML table")
        clean_name = str(name).strip()
        if not clean_name:
            raise ValueError("engine name must not be empty")
        base = engines.get(clean_name)
        default_bin = base.bin if base else clean_name
        bin_path = str(section.get("bin", default_bin)).strip()
        if not bin_path:
            raise ValueError(f"engines.{clean_name}.bin must not be empty")
        args_template = as_string_tuple(
            section.get("args_template", list(base.args_template) if base else None),
            key=f"engines.{clean_name}.args_template",
        )
        if not args_template:
            raise ValueError(f"engines.{clean_name}.args_template must not be empty")
        full_access_args = as_string_tuple(
            section.get("full_access_args", list(base.full_access_args) if base else []),
            key=f"engines.{clean_name}.full_access_args",
        )
        sandbox_args = as_string_tuple(
            section.get("sandbox_args", list(base.sandbox_args) if base else []),
            key=f"engines.{clean_name}.sandbox_args",
        )
        token_regex = optional_string(section.get("token_regex"))
        if token_regex is None and base is not None:
            token_regex = base.token_regex
        if token_regex:
            try:
                re.compile(token_regex, flags=re.IGNORECASE)
            except re.error as exc:
                raise ValueError(f"engines.{clean_name}.token_regex is invalid: {exc}") from exc
        model_report_regex = optional_string(section.get("model_report_regex"))
        if model_report_regex is None and base is not None:
            model_report_regex = base.model_report_regex
        if model_report_regex:
            try:
                compiled_report = re.compile(model_report_regex, flags=re.IGNORECASE)
            except re.error as exc:
                raise ValueError(
                    f"engines.{clean_name}.model_report_regex is invalid: {exc}"
                ) from exc
            if compiled_report.groups < 1:
                raise ValueError(
                    f"engines.{clean_name}.model_report_regex must have a capture group"
                )
        model_default = str(
            section.get("model_default", base.model_default if base else "")
        ).strip()
        engines[clean_name] = EngineConfig(
            name=clean_name,
            bin=bin_path,
            args_template=args_template,
            full_access_args=full_access_args,
            sandbox_args=sandbox_args,
            token_regex=token_regex,
            model_report_regex=model_report_regex,
            model_default=model_default,
        )
    return engines


@dataclass(frozen=True)
class TaskSpec:
    key: str
    spec: str
    check: str
    engine: str = DEFAULT_ENGINE_NAME
    expect_files: tuple[str, ...] = ()
    timeout_s: int = DEFAULT_TIMEOUT_S
    full_access: bool = False
    engine_args: tuple[str, ...] = ()
    verified: str = ""
    # Which model a harness engine should run for this task (fills the
    # engine's {model} placeholder); empty means the engine's model_default.
    model: str = ""
    task_type: str = ""

    @classmethod
    def from_obj(cls, obj: dict[str, Any]) -> "TaskSpec":
        key_raw = obj.get("key", "")
        if not isinstance(key_raw, str):
            raise ValueError("task key must be a string")
        key = key_raw.strip()
        if not key:
            raise ValueError("task key is required")
        spec = obj.get("spec", "")
        if not isinstance(spec, str):
            raise ValueError(f"task {key}: spec must be a string")
        if not spec:
            raise ValueError(f"task {key}: spec is required")
        check = obj.get("check", "")
        if not isinstance(check, str):
            raise ValueError(f"task {key}: check must be a string")
        if not check:
            raise ValueError(f"task {key}: check is required")
        expect_files = obj.get("expect_files", [])
        if not isinstance(expect_files, list):
            raise ValueError(f"task {key}: expect_files must be a list")
        engine = str(obj.get("engine", DEFAULT_ENGINE_NAME)).strip()
        if not engine:
            raise ValueError(f"task {key}: engine must not be empty")
        timeout_s = int(obj.get("timeout_s", DEFAULT_TIMEOUT_S))
        if timeout_s <= 0:
            raise ValueError(f"task {key}: timeout_s must be positive")
        engine_args = obj.get("engine_args", [])
        if not isinstance(engine_args, list) or not all(isinstance(item, str) for item in engine_args):
            raise ValueError(f"task {key}: engine_args must be a list of strings")
        verified = obj.get("verified", "")
        if not isinstance(verified, str):
            raise ValueError(f"task {key}: verified must be a string (plain-English description of what the check proves)")
        model = obj.get("model", "")
        if not isinstance(model, str):
            raise ValueError(f"task {key}: model must be a string (e.g. 'openrouter/z-ai/glm-5.2')")
        task_type = obj.get("task_type", "")
        if not isinstance(task_type, str):
            raise ValueError(f"task {key}: task_type must be a string")
        return cls(
            key=key,
            spec=spec,
            check=check,
            engine=engine,
            expect_files=tuple(str(item) for item in expect_files),
            timeout_s=timeout_s,
            full_access=bool(obj.get("full_access", False)),
            engine_args=tuple(engine_args),
            verified=verified.strip(),
            model=model.strip(),
            task_type=task_type.strip(),
        )


@dataclass(frozen=True)
class Manifest:
    run_name: str
    workdir: Path
    max_parallel: int
    worktrees: bool
    repo: Path | None
    tasks: tuple[TaskSpec, ...]
    source_path: Path | None = None

    @classmethod
    def from_path(cls, path: Path) -> "Manifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("manifest root must be a JSON object")
        manifest = cls.from_obj(data)
        return cls(
            run_name=manifest.run_name,
            workdir=manifest.workdir,
            max_parallel=manifest.max_parallel,
            worktrees=manifest.worktrees,
            repo=manifest.repo,
            tasks=manifest.tasks,
            source_path=path,
        )

    @classmethod
    def from_obj(cls, obj: dict[str, Any]) -> "Manifest":
        run_name = str(obj.get("run_name", "")).strip()
        if not run_name:
            raise ValueError("run_name is required")
        if run_name == MODEL_SCOREBOARD_RUN_NAME:
            raise ValueError("run_name model-scoreboard is reserved for the scoreboard page")
        workdir_raw = obj.get("workdir")
        if not workdir_raw:
            raise ValueError("workdir is required")
        workdir = Path(str(workdir_raw)).expanduser().resolve()
        max_parallel = int(obj.get("max_parallel", 1))
        if max_parallel <= 0:
            raise ValueError("max_parallel must be positive")
        repo_raw = obj.get("repo")
        repo = Path(str(repo_raw)).expanduser().resolve() if repo_raw else None
        tasks_raw = obj.get("tasks")
        if not isinstance(tasks_raw, list) or not tasks_raw:
            raise ValueError("tasks must be a non-empty list")
        tasks = tuple(TaskSpec.from_obj(task) for task in tasks_raw)
        keys = [task.key for task in tasks]
        duplicates = sorted({key for key in keys if keys.count(key) > 1})
        if duplicates:
            raise ValueError(f"duplicate task keys: {', '.join(duplicates)}")
        worktrees = bool(obj.get("worktrees", False))
        if worktrees:
            reserved_logs_dir = (workdir / "logs").resolve()
            collisions = []
            for task in tasks:
                taskdir = (workdir / task.key).resolve()
                if taskdir == reserved_logs_dir or reserved_logs_dir in taskdir.parents:
                    collisions.append(task.key)
            if collisions:
                raise ValueError(
                    "task key(s) collide with reserved worktree logs directory "
                    f"'logs': {', '.join(collisions)}"
                )
        return cls(
            run_name=run_name,
            workdir=workdir,
            max_parallel=max_parallel,
            worktrees=worktrees,
            repo=repo,
            tasks=tasks,
        )

    def with_max_parallel(self, value: int | None) -> "Manifest":
        if value is None:
            return self
        if value <= 0:
            raise ValueError("--max-parallel must be positive")
        return Manifest(
            run_name=self.run_name,
            workdir=self.workdir,
            max_parallel=value,
            worktrees=self.worktrees,
            repo=self.repo,
            tasks=self.tasks,
            source_path=self.source_path,
        )


FILE_TEST_OPS = {"-e", "-f", "-s", "-d", "-r", "-w", "-x", "-L"}


def lint_manifest(manifest: Manifest, *, include_model_log_nudges: bool = False) -> list[str]:
    findings: list[str] = []
    if manifest.run_name == MODEL_SCOREBOARD_RUN_NAME:
        findings.append("manifest: run_name model-scoreboard is reserved for the scoreboard page.")

    for task in manifest.tasks:
        if check_cannot_fail(task.check):
            findings.append(f"{task.key}: check cannot fail, so the task cannot be verified.")
        if check_may_fail_silently(task.check):
            findings.append(
                f"{task.key}: check may fail without printing why; retry prompt and eval log depend on failure output."
            )
        if manifest.worktrees and any(is_relative_expect_file(path) for path in task.expect_files):
            findings.append(
                f"{task.key}: deliverable would be deleted with the worktree; write it outside the worktree or export it in the check."
            )
        if manifest.worktrees and instructs_git_commit(task.spec):
            findings.append(
                f"{task.key}: worker commits die with the worktree; have the worker leave changes uncommitted and export the diff in the check."
            )
        if len(task.spec.strip()) < 80:
            findings.append(
                f"{task.key}: spec is probably underspecified; workers are stateless and cannot ask questions."
            )
        if spec_is_file_pointer(task.spec):
            findings.append(
                f"{task.key}: spec is a pointer to an instruction file; anyone watching Ringside "
                "sees no real brief and the retry prompt loses context — put the instructions in the spec itself."
            )
        if not task.expect_files and not manifest.worktrees:
            findings.append(
                f"{task.key}: no expect_files; the results page will guess deliverables from the "
                "task folder — declare them so the reader sees exactly the right work."
            )
        if not task.verified:
            findings.append(
                f"{task.key}: no 'verified' description; a reader of the results page sees "
                "'checked' but not what the check proves — add one plain-English sentence."
            )
        if include_model_log_nudges and not task.task_type:
            findings.append(
                f"{task.key}: no task_type; the model log buckets this as (untyped) — "
                "name one (e.g. code-feature, research, image-gen) so './ringer.py models' can guide routing."
            )

    if len(manifest.tasks) >= 3 and manifest.max_parallel == 1:
        findings.append("manifest: tasks will run serially; set max_parallel.")

    if not manifest.worktrees:
        # Relative expect_files resolve inside each task's own directory and
        # cannot collide; only a shared absolute path is a real collision.
        paths_to_tasks: dict[str, list[str]] = {}
        for task in manifest.tasks:
            for path in task.expect_files:
                if not Path(path).expanduser().is_absolute():
                    continue
                paths_to_tasks.setdefault(path, []).append(task.key)
        for path, task_keys in paths_to_tasks.items():
            if len(task_keys) >= 2:
                findings.append(
                    f"manifest: write collision on {path}: listed by {', '.join(task_keys)}."
                )

    return findings


FILE_POINTER_SPEC_RE = re.compile(
    r"\b(read|open|follow|see)\b[^\n.]{0,100}?/[\w~][\w./~-]*",
    re.IGNORECASE,
)


def spec_is_file_pointer(spec: str) -> bool:
    """True when the spec's substance lives in some other file.

    'Read /path/to/instructions.md and do what it says' hides the brief from
    everyone watching the run and starves the retry prompt. Long specs that
    merely reference files for CONTEXT are fine — the heuristic only fires
    when the spec is short enough that the pointer must be the whole plan.
    """
    text = spec.strip()
    if re.search(r"do (exactly )?what (it|the file|that file) says", text, re.IGNORECASE):
        return True
    if len(text) >= 600:
        return False
    return bool(FILE_POINTER_SPEC_RE.search(text))


def check_cannot_fail(check: str) -> bool:
    stripped = strip_shell_comments(check).strip()
    if stripped in {"true", ":", "exit 0"}:
        return True
    return consists_only_of_echo_commands(stripped)


def strip_shell_comments(command: str) -> str:
    result: list[str] = []
    in_single = False
    in_double = False
    escaped = False
    i = 0
    while i < len(command):
        char = command[i]
        if escaped:
            result.append(char)
            escaped = False
            i += 1
            continue
        if char == "\\" and not in_single:
            result.append(char)
            escaped = True
            i += 1
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            result.append(char)
            i += 1
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            result.append(char)
            i += 1
            continue
        if (
            char == "#"
            and not in_single
            and not in_double
            and (not result or result[-1].isspace())
        ):
            while i < len(command) and command[i] != "\n":
                i += 1
            continue
        result.append(char)
        i += 1
    return "".join(result)


def consists_only_of_echo_commands(command: str) -> bool:
    if not command or "||" in command or re.search(r"[|<>]", command):
        return False
    parts = [part.strip() for part in re.split(r"(?:&&|;|\n)+", command) if part.strip()]
    if not parts:
        return False
    for part in parts:
        try:
            tokens = shlex.split(part)
        except ValueError:
            return False
        if not tokens or tokens[0] != "echo":
            return False
    return True


def check_may_fail_silently(check: str) -> bool:
    stripped = strip_shell_comments(check).strip()
    if has_quiet_diff_probe(stripped):
        return not has_failure_output_branch(stripped)
    if not stripped or "||" in stripped:
        return False
    if re.search(r"(?:;|\n|\|)", stripped):
        return False
    parts = [part.strip() for part in stripped.split("&&") if part.strip()]
    return bool(parts) and all(is_silent_probe(part) for part in parts)


def has_quiet_diff_probe(command: str) -> bool:
    return any(has_command_prefix(part, ("diff", "-q")) for part in command_parts(command))


def has_failure_output_branch(command: str) -> bool:
    if "||" not in command:
        return False
    branch = command.split("||", 1)[1]
    return any(
        has_command_prefix(part, (prefix,))
        for part in command_parts(branch)
        for prefix in ("echo", "printf", "cat", "diff", "ls")
    )


def command_parts(command: str) -> list[str]:
    return [part.strip(" \t{}()") for part in re.split(r"(?:&&|\|\||;|\n)+", command) if part.strip()]


def has_command_prefix(command: str, prefix: tuple[str, ...]) -> bool:
    try:
        tokens = shlex.split(strip_common_redirections(command))
    except ValueError:
        return False
    return len(tokens) >= len(prefix) and tuple(tokens[: len(prefix)]) == prefix


def is_silent_probe(command: str) -> bool:
    return is_file_existence_test(command) or is_quiet_grep(command)


def is_quiet_grep(command: str) -> bool:
    command = strip_common_redirections(command.strip())
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    return bool(tokens) and tokens[0] == "grep" and any(
        token == "-q" or (token.startswith("-") and "q" in token[1:]) for token in tokens[1:]
    )


def is_file_existence_test(command: str) -> bool:
    command = strip_common_redirections(command.strip())
    try:
        tokens = shlex.split(command)
    except ValueError:
        return False
    if len(tokens) >= 3 and tokens[0] == "test" and tokens[1] in FILE_TEST_OPS:
        return True
    return len(tokens) >= 4 and tokens[0] == "[" and tokens[1] in FILE_TEST_OPS and tokens[-1] == "]"


def strip_common_redirections(command: str) -> str:
    command = re.sub(r"\s+\d?>&\d+\s*$", "", command)
    command = re.sub(r"\s+\d?>\S+\s*$", "", command)
    return command.strip()


def is_relative_expect_file(path: str) -> bool:
    return bool(path.strip()) and not path.startswith("~") and not Path(path).is_absolute()


def instructs_git_commit(spec: str) -> bool:
    lower = spec.lower()
    start = 0
    while True:
        index = lower.find("git commit", start)
        if index == -1:
            return False
        prefix = lower[max(0, index - 48) : index]
        if not is_negated_git_commit(prefix):
            return True
        start = index + len("git commit")


def is_negated_git_commit(prefix: str) -> bool:
    separators = r"[\s`'\"()\[\]{}:;,.!?-]*"
    return bool(
        re.search(
            rf"(?:do\s+not|don't|never|no){separators}(?:run{separators})?$",
            prefix,
        )
    )


@dataclass
class TaskRuntime:
    task: TaskSpec
    taskdir: Path
    log_path: Path
    report_paths: dict[str, Path] = field(default_factory=dict)
    deliverables: list[dict[str, Any]] = field(default_factory=list)
    deliverable_notes: list[str] = field(default_factory=list)
    status: str = "queued"
    spec_short: str = ""
    attempts: int = 0
    started_at_monotonic: float | None = None
    ended_at_monotonic: float | None = None
    worker_pid: int | None = None
    tokens: int | None = None
    final_verdict: str | None = None
    last_check_returncode: int | None = None
    last_check_timed_out: bool = False
    last_check_output: str = ""
    last_worker_command: list[str] = field(default_factory=list)
    steering: dict[str, Any] | None = None

    def elapsed_s(self, now: float) -> float:
        if self.started_at_monotonic is None:
            return 0.0
        end = self.ended_at_monotonic if self.ended_at_monotonic is not None else now
        return max(0.0, end - self.started_at_monotonic)


@dataclass(frozen=True)
class WorkerResult:
    returncode: int | None
    timed_out: bool
    tokens: int | None
    error: str | None = None
    reported_model: str | None = None


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    check_returncode: int | None
    check_timed_out: bool
    raw_output_excerpt: str
    missing_files: tuple[str, ...] = ()


class ProcessTree:
    @staticmethod
    def read() -> tuple[dict[int, list[int]], dict[int, str]]:
        try:
            proc = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,args="],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=5,
            )
        except Exception:
            return {}, {}
        children: dict[int, list[int]] = {}
        commands: dict[int, str] = {}
        for line in proc.stdout.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[0])
                ppid = int(parts[1])
            except ValueError:
                continue
            command = parts[2] if len(parts) > 2 else ""
            children.setdefault(ppid, []).append(pid)
            commands[pid] = command
        return children, commands

    @staticmethod
    def count_named_descendants(
        root_pid: int | None,
        children: dict[int, list[int]],
        commands: dict[int, str],
        process_name: str,
    ) -> int:
        if root_pid is None:
            return 0
        needle = process_name.lower()
        count = 0
        stack = list(children.get(root_pid, []))
        while stack:
            pid = stack.pop()
            command = commands.get(pid, "")
            if command:
                executable = Path(command.split()[0]).name.lower()
                if needle and needle in executable:
                    count += 1
            stack.extend(children.get(pid, []))
        return count


class StateWriter:
    def __init__(
        self,
        run_id: str,
        run_name: str,
        identity: str,
        state_dir: Path,
        engines: dict[str, EngineConfig],
        started_at: datetime,
        runtimes: list[TaskRuntime],
        lock: threading.RLock,
        max_parallel: int = 1,
        artifact: ArtifactConfig | None = None,
        path: Path | None = None,
    ) -> None:
        self.run_id = run_id
        self.run_name = run_name
        self.identity = identity
        self.engines = engines
        self.started_at = started_at
        self.runtimes = runtimes
        self.lock = lock
        self.max_parallel = max_parallel
        self.state_dir = state_dir
        self.path = path or (state_dir / "runs" / f"{run_id}.json")
        self.pid = os.getpid()
        self.port: int | None = None
        self.finished = False
        self.summary: dict[str, int] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.artifact = artifact or ArtifactConfig(
            enabled=False,
            out_template=str(state_dir / "artifacts" / "{run_id}.html"),
            report_template=str(state_dir / "artifacts" / "{run_id}-report.html"),
            index_out=state_dir / "artifacts" / "index.html",
        )
        self.artifact_path = self.artifact.artifact_path(self.run_id, self.run_name)
        self.live_path = artifact_live_path(self.state_dir, self.run_name)
        self.version_path = artifact_version_path(self.state_dir, self.run_name, self.run_id)
        self.report_path = self.artifact.report_path(self.run_id, self.run_name)
        self.artifact_renderer = ArtifactRenderer(self.artifact_path)
        self.report_written = False
        self.version_recorded = False
        self._last_library_state: str | None = None
        self._last_library_write_monotonic = 0.0

    def start(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        if self.artifact.enabled:
            self._reconcile_library_safe()
        self.flush()
        self._thread = threading.Thread(target=self._loop, name="ringer-state-writer", daemon=True)
        self._thread.start()

    def set_port(self, port: int | None) -> None:
        self.port = port
        self.flush()

    def finish(self) -> None:
        self.finished = True
        self.summary = self.build_summary()
        state = self.flush()
        self._write_final_report_safe(state)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2)
        self.flush()

    def flush(self) -> dict[str, Any]:
        state = self.snapshot()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.path)
        if self.artifact.enabled:
            self._write_status_artifact_safe(state)
            self._write_index_safe()
            self._write_library_live_safe(state)
        return state

    def snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        children, commands = ProcessTree.read()
        with self.lock:
            tasks = []
            for runtime in self.runtimes:
                log_tail = tail_lines(runtime.log_path, line_count=3)
                log_tail_full = tail_lines(runtime.log_path, line_count=40)
                engine = self.engines.get(runtime.task.engine)
                process_name = engine.process_name if engine else runtime.task.engine
                task_state = {
                    "key": runtime.task.key,
                    "status": runtime.status,
                    "verdict": runtime.final_verdict,
                    "engine": runtime.task.engine,
                    "model": (
                        runtime.task.model
                        or (engine.model_default if engine else "")
                        or effective_model_from_command(runtime.last_worker_command)
                    ),
                    "spec": runtime.task.spec,
                    "spec_short": runtime.spec_short,
                    "verified": runtime.task.verified,
                    "check": runtime.task.check,
                    "check_returncode": runtime.last_check_returncode,
                    "check_timed_out": runtime.last_check_timed_out,
                    "check_output_tail": shorten(runtime.last_check_output, 4000),
                    "timeout_s": runtime.task.timeout_s,
                    "taskdir": str(runtime.taskdir),
                    "log_path": str(runtime.log_path),
                    "report_paths": {
                        name: str(path) for name, path in runtime.report_paths.items()
                    },
                    "deliverables": [dict(item) for item in runtime.deliverables],
                    "deliverable_notes": list(runtime.deliverable_notes),
                    "activity": worker_activity(runtime.log_path, log_tail),
                    "elapsed_s": round(runtime.elapsed_s(now), 1),
                    "tokens": runtime.tokens,
                    "attempts": runtime.attempts,
                    "children": ProcessTree.count_named_descendants(
                        runtime.worker_pid, children, commands, process_name
                    ),
                    "log_tail": log_tail,
                    "log_tail_full": log_tail_full,
                }
                if runtime.steering is not None:
                    task_state["steering"] = dict(runtime.steering)
                tasks.append(task_state)
            pass_count = sum(1 for item in tasks if item["status"] == "pass")
            fail_count = sum(1 for item in tasks if item["status"] == "fail")
            running_count = sum(
                1 for item in tasks if item["status"] in {"running", "verifying", "retrying"}
            )
            totals = {
                "running": running_count,
                "done": pass_count + fail_count,
                "pass": pass_count,
                "fail": fail_count,
                "tokens": sum(int(item["tokens"] or 0) for item in tasks),
            }
            return {
                "run_id": self.run_id,
                "run_name": self.run_name,
                "identity": self.identity,
                "state": "finished" if self.finished else "live",
                "pid": self.pid,
                "port": self.port,
                "dashboard_port": self.port,
                "max_parallel": self.max_parallel,
                "finished": self.finished,
                "summary": self.summary if self.finished else None,
                "started_at": self.started_at.isoformat(),
                "elapsed_s": max((float(item["elapsed_s"]) for item in tasks), default=0.0),
                "tasks": tasks,
                "totals": totals,
                "pass": totals["pass"],
                "fail": totals["fail"],
                "tokens": totals["tokens"],
                "artifact_path": str(self.artifact_path) if self.artifact.enabled else None,
                "live_path": str(self.live_path) if self.artifact.enabled else None,
                "report_path": str(self.report_path) if self.artifact.enabled else None,
                "report_ready": self.report_written,
            }

    def build_summary(self) -> dict[str, int]:
        with self.lock:
            return {
                "pass": sum(1 for runtime in self.runtimes if runtime.status == "pass"),
                "fail": sum(1 for runtime in self.runtimes if runtime.status == "fail"),
                "tokens": sum(int(runtime.tokens or 0) for runtime in self.runtimes),
            }

    def _write_status_artifact_safe(self, state: dict[str, Any]) -> None:
        try:
            if bool(state.get("finished")) or str(state.get("state")) == "finished":
                artifact_html = self.artifact_renderer.render_final_report_html(
                    state,
                    page_path=self.artifact_path,
                )
                live_html = self.artifact_renderer.render_final_report_html(
                    state,
                    page_path=self.live_path,
                )
            else:
                artifact_html = self.artifact_renderer.render_status_html(
                    state,
                    page_path=self.artifact_path,
                )
                live_html = self.artifact_renderer.render_status_html(
                    state,
                    page_path=self.live_path,
                )
            atomic_write_text(self.artifact_path, artifact_html)
            atomic_write_text(self.live_path, live_html)
        except Exception as exc:
            print(f"artifact render error (status page, non-fatal): {exc}", file=sys.stderr)

    def _write_final_report_safe(self, state: dict[str, Any]) -> None:
        if not self.artifact.enabled:
            return
        try:
            report_html = self.artifact_renderer.render_final_report_html(
                state,
                page_path=self.report_path,
            )
            version_html = self.artifact_renderer.render_final_report_html(
                state,
                page_path=self.version_path,
            )
            atomic_write_text(self.report_path, report_html)
            atomic_write_text(self.version_path, version_html)
            self.report_written = True
            self._append_library_version_safe(state)
            # Re-flush the plain state JSON so report_ready/report_path are accurate for
            # anything (Ringside) polling the state file right after the run ends.
            tmp = self.path.with_suffix(".json.tmp")
            state = dict(state)
            state["report_ready"] = True
            tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as exc:
            print(f"artifact render error (final report, non-fatal): {exc}", file=sys.stderr)

    def _write_library_live_safe(self, state: dict[str, Any]) -> None:
        outcome = artifact_outcome_from_state(state)
        now = time.monotonic()
        if self._last_library_state == outcome and now - self._last_library_write_monotonic < 5:
            return
        try:
            update_artifact_library_live(
                self.state_dir,
                run_name=self.run_name,
                run_id=self.run_id,
                identity=self.identity,
                state=outcome,
            )
            self._last_library_state = outcome
            self._last_library_write_monotonic = now
        except Exception as exc:
            print(f"artifact library update error (non-fatal): {exc}", file=sys.stderr)

    def _append_library_version_safe(self, state: dict[str, Any]) -> None:
        if self.version_recorded:
            return
        totals = state.get("totals") if isinstance(state.get("totals"), dict) else {}
        outcome = artifact_outcome_from_state(state)
        try:
            append_artifact_library_version(
                self.state_dir,
                run_name=self.run_name,
                run_id=self.run_id,
                identity=self.identity,
                outcome=outcome,
                version_path=self.version_path,
                report_path=self.report_path if self.report_path != self.version_path else None,
                tasks_pass=int(totals.get("pass", state.get("pass", 0)) or 0),
                tasks_fail=int(totals.get("fail", state.get("fail", 0)) or 0),
                deliverables=collect_state_deliverables(state),
            )
            self.version_recorded = True
            self._last_library_state = outcome
            self._last_library_write_monotonic = time.monotonic()
        except Exception as exc:
            print(f"artifact library version error (non-fatal): {exc}", file=sys.stderr)

    def _reconcile_library_safe(self) -> None:
        try:
            reconcile_artifact_library_dead_runs(self.state_dir)
        except Exception as exc:
            print(f"artifact library reconcile error (non-fatal): {exc}", file=sys.stderr)

    def _write_index_safe(self) -> None:
        try:
            entries = scan_run_states(self.state_dir)
            html = self.artifact_renderer.render_artifact_index_html(entries)
            atomic_write_text(self.artifact.index_out, html)
        except Exception as exc:
            print(f"artifact render error (index, non-fatal): {exc}", file=sys.stderr)

    def _loop(self) -> None:
        while not self._stop.wait(1.0):
            try:
                self.flush()
            except Exception as exc:
                print(f"state writer error: {exc}", file=sys.stderr)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd: int | None = None
    tmp_path: Path | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = None
            fh.write(text)
            fh.flush()
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink()


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def ringer_home() -> Path:
    value = os.environ.get(f"{ENV_VAR_PREFIX}_HOME")
    if value and value.strip():
        return Path(value).expanduser().resolve()
    return (Path.home() / STATE_DIR_NAME).resolve()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_catalog_path() -> Path:
    return ringer_home() / "openrouter-catalog.json"


def catalog_changes_path(snapshot_path: Path) -> Path:
    text = str(snapshot_path)
    if text.endswith(".json"):
        return Path(text[:-5] + ".changes.jsonl")
    return snapshot_path.with_name(snapshot_path.name + ".changes.jsonl")


def catalog_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value).strip() or "0")
    except (InvalidOperation, ValueError):
        return Decimal("0")


def catalog_decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def catalog_per_m(value: Any) -> float:
    return float(catalog_decimal(value) * Decimal("1000000"))


def catalog_per_m_decimal(value: Decimal) -> float:
    return float(value * Decimal("1000000"))


def catalog_price_equal(left: Any, right: Any) -> bool:
    return catalog_decimal(left) == catalog_decimal(right)


def catalog_price_is_negative(value: Any) -> bool:
    return catalog_decimal(value) < 0


def normalize_catalog_model(raw: dict[str, Any], *, fetched_at: str) -> dict[str, Any]:
    pricing = raw.get("pricing")
    pricing_obj = pricing if isinstance(pricing, dict) else {}
    architecture = raw.get("architecture")
    architecture_obj = architecture if isinstance(architecture, dict) else {}
    model_id = str(raw.get("id", "")).strip()
    prompt_price = catalog_decimal_or_none(pricing_obj.get("prompt"))
    completion_price = catalog_decimal_or_none(pricing_obj.get("completion"))
    pricing_unknown = prompt_price is None or completion_price is None
    variable_pricing = pricing_unknown or prompt_price < 0 or completion_price < 0
    prompt_per_m = None if variable_pricing else catalog_per_m_decimal(prompt_price)
    completion_per_m = None if variable_pricing else catalog_per_m_decimal(completion_price)
    is_free = not variable_pricing and (
        model_id.endswith(":free")
        or (
            prompt_price == 0
            and completion_price == 0
        )
    )
    if model_id.endswith(":free"):
        is_free = True
    context_length_raw = raw.get("context_length")
    try:
        context_length = int(context_length_raw)
    except (TypeError, ValueError):
        context_length = 0
    return {
        "id": model_id,
        "name": str(raw.get("name", "")).strip() or model_id,
        "context_length": context_length,
        "modality": str(architecture_obj.get("modality", "")).strip(),
        "pricing": dict(pricing_obj),
        "prompt_per_m": prompt_per_m,
        "completion_per_m": completion_per_m,
        "variable_pricing": variable_pricing,
        "pricing_unknown": pricing_unknown,
        "free": is_free,
        "fetched_at": fetched_at,
    }


def normalize_catalog_payload(payload: dict[str, Any], *, fetched_at: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list):
        raise ValueError("catalog source must have a JSON object with a data array")
    models: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        model = normalize_catalog_model(item, fetched_at=fetched_at)
        if model["id"]:
            models.append(model)
    return sorted(models, key=catalog_sort_key)


def catalog_sort_key(model: dict[str, Any]) -> tuple[bool, float, str]:
    variable_pricing = bool(model.get("variable_pricing"))
    return (
        variable_pricing,
        float("inf")
        if variable_pricing
        else float(model.get("prompt_per_m") or 0) + float(model.get("completion_per_m") or 0),
        str(model.get("id") or ""),
    )


def fetch_catalog_payload(source: str, *, timeout: float = CATALOG_FETCH_TIMEOUT_S) -> dict[str, Any]:
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme in {"http", "https"}:
        request = urllib.request.Request(source, headers={"User-Agent": "ringer.py"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    else:
        payload = json.loads(Path(source).expanduser().read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog source must be a JSON object")
    return payload


def load_catalog_snapshot(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    if isinstance(data, dict):
        models = data.get("models")
    else:
        models = data
    if not isinstance(models, list):
        return []
    return [item for item in models if isinstance(item, dict)]


def catalog_event_model_details(model: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": model.get("name", ""),
        "prompt_per_m": model.get("prompt_per_m", 0),
        "completion_per_m": model.get("completion_per_m", 0),
        "variable_pricing": bool(model.get("variable_pricing")),
        "pricing_unknown": bool(model.get("pricing_unknown")),
        "free": bool(model.get("free")),
        "context_length": model.get("context_length", 0),
        "modality": model.get("modality", ""),
    }


def diff_catalog_snapshots(
    old_models: list[dict[str, Any]],
    new_models: list[dict[str, Any]],
    *,
    ts: str,
) -> list[dict[str, Any]]:
    old_by_id = {str(model.get("id")): model for model in old_models if model.get("id")}
    new_by_id = {str(model.get("id")): model for model in new_models if model.get("id")}
    events: list[dict[str, Any]] = []

    for model_id in sorted(new_by_id.keys() - old_by_id.keys()):
        model = new_by_id[model_id]
        events.append({"ts": ts, "kind": "added", "id": model_id, **catalog_event_model_details(model)})

    for model_id in sorted(old_by_id.keys() - new_by_id.keys()):
        model = old_by_id[model_id]
        events.append({"ts": ts, "kind": "removed", "id": model_id, **catalog_event_model_details(model)})

    for model_id in sorted(old_by_id.keys() & new_by_id.keys()):
        old = old_by_id[model_id]
        new = new_by_id[model_id]
        old_prompt = old.get("prompt_per_m", 0)
        new_prompt = new.get("prompt_per_m", 0)
        old_completion = old.get("completion_per_m", 0)
        new_completion = new.get("completion_per_m", 0)
        old_free = bool(old.get("free"))
        new_free = bool(new.get("free"))
        old_variable = bool(old.get("variable_pricing"))
        new_variable = bool(new.get("variable_pricing"))
        if new_variable:
            if not old_variable:
                events.append(
                    {
                        "ts": ts,
                        "kind": "pricing_variable",
                        "id": model_id,
                        "name": new.get("name", old.get("name", "")),
                        "old_prompt_per_m": old_prompt,
                        "new_prompt_per_m": new_prompt,
                        "old_completion_per_m": old_completion,
                        "new_completion_per_m": new_completion,
                        "old_free": old_free,
                        "new_free": new_free,
                    }
                )
            continue
        if old_variable:
            events.append(
                {
                    "ts": ts,
                    "kind": "pricing_fixed",
                    "id": model_id,
                    "name": new.get("name", old.get("name", "")),
                    "old_prompt_per_m": old_prompt,
                    "new_prompt_per_m": new_prompt,
                    "old_completion_per_m": old_completion,
                    "new_completion_per_m": new_completion,
                    "old_free": old_free,
                    "new_free": new_free,
                }
            )
            if new_free:
                events.append(
                    {
                        "ts": ts,
                        "kind": "went_free",
                        "id": model_id,
                        "name": new.get("name", old.get("name", "")),
                        "old_prompt_per_m": old_prompt,
                        "new_prompt_per_m": new_prompt,
                        "old_completion_per_m": old_completion,
                        "new_completion_per_m": new_completion,
                    }
                )
            continue
        price_changed = old_prompt != new_prompt or old_completion != new_completion
        if price_changed:
            events.append(
                {
                    "ts": ts,
                    "kind": "price_change",
                    "id": model_id,
                    "name": new.get("name", old.get("name", "")),
                    "old_prompt_per_m": old_prompt,
                    "new_prompt_per_m": new_prompt,
                    "old_completion_per_m": old_completion,
                    "new_completion_per_m": new_completion,
                    "old_free": old_free,
                    "new_free": new_free,
                }
            )
        if old_free != new_free:
            events.append(
                {
                    "ts": ts,
                    "kind": "went_free" if new_free else "went_paid",
                    "id": model_id,
                    "name": new.get("name", old.get("name", "")),
                    "old_prompt_per_m": old_prompt,
                    "new_prompt_per_m": new_prompt,
                    "old_completion_per_m": old_completion,
                    "new_completion_per_m": new_completion,
                }
            )
    return events


@dataclass(frozen=True)
class CatalogRefreshResult:
    path: Path
    changes_path: Path
    models: list[dict[str, Any]]
    events: list[dict[str, Any]]


def append_catalog_events(path: Path, events: list[dict[str, Any]]) -> None:
    if not events:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True) + "\n")


@contextlib.contextmanager
def catalog_refresh_lock(snapshot_path: Path) -> Iterable[None]:
    lock_path = snapshot_path.with_name(snapshot_path.name + ".lock")
    try:
        import fcntl
    except Exception:
        yield
        return
    fh = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = lock_path.open("a", encoding="utf-8")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except Exception:
        if fh is not None:
            with contextlib.suppress(Exception):
                fh.close()
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def refresh_openrouter_catalog(
    snapshot_path: Path,
    *,
    source: str = DEFAULT_CATALOG_SOURCE,
    timeout: float = CATALOG_FETCH_TIMEOUT_S,
) -> CatalogRefreshResult:
    snapshot_path = snapshot_path.expanduser().resolve()
    changes_path = catalog_changes_path(snapshot_path)
    with catalog_refresh_lock(snapshot_path):
        ts = utc_now_iso()
        old_models = load_catalog_snapshot(snapshot_path)
        payload = fetch_catalog_payload(source, timeout=timeout)
        new_models = normalize_catalog_payload(payload, fetched_at=ts)
        events = diff_catalog_snapshots(old_models, new_models, ts=ts)
        snapshot = {"fetched_at": ts, "models": new_models}
        append_catalog_events(changes_path, events)
        # Append events before replacing the snapshot: a crash here can duplicate
        # events on the next refresh, but duplicated events are recoverable and
        # silently lost catalog changes are not.
        atomic_write_json(snapshot_path, snapshot)
    return CatalogRefreshResult(
        path=snapshot_path,
        changes_path=changes_path,
        models=new_models,
        events=events,
    )


def format_catalog_price(value: Any, *, variable: bool = False) -> str:
    if variable or value is None:
        return "var"
    amount = float(value or 0)
    if amount == 0:
        return "0"
    if amount < 0.01:
        return f"{amount:.4f}".rstrip("0").rstrip(".")
    return f"{amount:.2f}".rstrip("0").rstrip(".")


def print_catalog_table(models: list[dict[str, Any]]) -> None:
    header = f"{'id':<48} {'$/M in':>9} {'$/M out':>9} {'ctx':>8} {'FREE':<4}"
    print(header)
    print("-" * len(header))
    for model in sorted(models, key=catalog_sort_key):
        variable_pricing = bool(model.get("variable_pricing"))
        marker = "FREE" if model.get("free") else ""
        print(
            f"{shorten(str(model.get('id', '')), 48):<48} "
            f"{format_catalog_price(model.get('prompt_per_m'), variable=variable_pricing):>9} "
            f"{format_catalog_price(model.get('completion_per_m'), variable=variable_pricing):>9} "
            f"{int(model.get('context_length') or 0):>8} {marker:<4}"
        )


def read_catalog_events(path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    events: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(events) >= limit:
            break
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def describe_catalog_event(event: dict[str, Any]) -> str:
    kind = str(event.get("kind", "event"))
    model_id = str(event.get("id", ""))
    ts = str(event.get("ts", ""))
    if kind == "price_change":
        return (
            f"{ts} {model_id} price_change: "
            f"in {format_catalog_price(event.get('old_prompt_per_m'))}"
            f" -> {format_catalog_price(event.get('new_prompt_per_m'))}, "
            f"out {format_catalog_price(event.get('old_completion_per_m'))}"
            f" -> {format_catalog_price(event.get('new_completion_per_m'))}"
        )
    if kind in {"went_free", "went_paid"}:
        return f"{ts} {model_id} {kind}"
    if kind == "pricing_variable":
        return f"{ts} {model_id} pricing_variable"
    if kind == "pricing_fixed":
        return (
            f"{ts} {model_id} pricing_fixed: "
            f"in {format_catalog_price(event.get('new_prompt_per_m'))}, "
            f"out {format_catalog_price(event.get('new_completion_per_m'))}"
        )
    if kind == "added":
        marker = " FREE" if event.get("free") else ""
        return f"{ts} {model_id} added{marker}"
    if kind == "removed":
        return f"{ts} {model_id} removed"
    return f"{ts} {model_id} {kind}"


def describe_catalog_event_humanized(event: dict[str, Any]) -> str:
    text = describe_catalog_event(event)
    ts = str(event.get("ts", ""))
    if ts and text.startswith(ts):
        return humanized_log_date(ts) + text[len(ts) :]
    return text


def run_catalog_command(args: argparse.Namespace) -> int:
    snapshot_path = (args.file or default_catalog_path()).expanduser().resolve()
    if args.refresh:
        models = refresh_openrouter_catalog(
            snapshot_path,
            source=args.source or DEFAULT_CATALOG_SOURCE,
        ).models
    else:
        models = load_catalog_snapshot(snapshot_path)

    if args.changes:
        for event in read_catalog_events(catalog_changes_path(snapshot_path)):
            print(describe_catalog_event(event))
        return 0

    if args.free:
        models = [model for model in models if model.get("free")]

    if args.json:
        print(json.dumps(sorted(models, key=catalog_sort_key)))
        return 0

    if not models:
        print(f"No catalog snapshot at {snapshot_path}. Run './ringer.py catalog --refresh'.", file=sys.stderr)
        return 1
    print_catalog_table(models)
    return 0


def catalog_snapshot_is_fresh(
    snapshot_path: Path,
    *,
    max_age_s: int = CATALOG_AUTO_REFRESH_MAX_AGE_S,
    now: float | None = None,
) -> bool:
    try:
        mtime = snapshot_path.expanduser().stat().st_mtime
    except OSError:
        return False
    return ((time.time() if now is None else now) - mtime) < max_age_s


def start_catalog_auto_refresh(
    *,
    snapshot_path: Path | None = None,
    source: str = DEFAULT_CATALOG_SOURCE,
    print_notice: bool = True,
) -> threading.Thread | None:
    try:
        if os.environ.get("RINGER_NO_CATALOG_REFRESH") == "1":
            return None
        path = (snapshot_path or default_catalog_path()).expanduser().resolve()
        if catalog_snapshot_is_fresh(path):
            return None
    except Exception:
        return None

    def worker() -> None:
        try:
            result = refresh_openrouter_catalog(path, source=source, timeout=CATALOG_FETCH_TIMEOUT_S)
            went_free = [event for event in result.events if event.get("kind") == "went_free"]
            if print_notice and went_free:
                sample = ", ".join(str(event.get("id")) for event in went_free[:3])
                extra = "" if len(went_free) <= 3 else f" and {len(went_free) - 3} more"
                print(f"Catalog refresh: model went FREE: {sample}{extra}", file=sys.stderr, flush=True)
        except Exception:
            pass

    try:
        thread = threading.Thread(target=worker, name="ringer-catalog-refresh", daemon=True)
        thread.start()
        return thread
    except Exception:
        return None


# Promotion ladder: 3+ tasks and first-try >= 2/3 ("2 of 3"). The exact
# fraction matters — 0.67 would misclassify a literal 2-of-3 record.
PROVEN_MIN_TASKS = 3
PROVEN_MIN_FIRST_TRY = 2 / 3


def proven_model_group(group: dict[str, Any]) -> bool:
    return (
        int(group.get("tasks") or 0) >= PROVEN_MIN_TASKS
        and float(group.get("first_try_pass_rate") or 0) >= PROVEN_MIN_FIRST_TRY
    )


def catalog_model_is_text_candidate(model: dict[str, Any]) -> bool:
    try:
        context_length = int(model.get("context_length") or 0)
    except (TypeError, ValueError):
        context_length = 0
    return (
        not bool(model.get("variable_pricing"))
        and str(model.get("modality", "")).strip().lower() == "text->text"
        and context_length >= 32000
    )


def catalog_explore_candidates(
    catalog_models: list[dict[str, Any]],
    *,
    tested_models: set[str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    candidates = [
        model
        for model in catalog_models
        if str(model.get("id", "")).strip() not in tested_models
        and str(model.get("id", "")).strip() not in RESERVED_FIXTURE_MODELS
        and catalog_model_is_text_candidate(model)
    ]
    return sorted(
        candidates,
        key=lambda model: (
            not bool(model.get("free")),
            float(model.get("prompt_per_m") or 0) + float(model.get("completion_per_m") or 0),
            str(model.get("id") or ""),
        ),
    )[:limit]


def print_model_explore(
    *,
    log_path: Path,
    rows_read: int,
    skipped: int,
    groups: list[dict[str, Any]],
    catalog_path: Path,
    catalog_models: list[dict[str, Any]],
) -> None:
    print(f"TIERS from {log_path} ({rows_read} rows, {skipped} skipped lines)")
    if not groups:
        print("  no local evidence")
    for group in groups:
        label = (
            "unranked"
            if group.get("unattributed")
            else ("proven" if proven_model_group(group) else "probation")
        )
        display = (
            f"{group.get('model_display') or UNATTRIBUTED_MODEL_DISPLAY} "
            f"[{group.get('engine') or 'unknown'}]"
            if group.get("unattributed")
            else str(group["model"])
        )
        print(
            f"  {label:<9} {display} "
            f"task_type={group['task_type']} tasks={group['tasks']} "
            f"first={group['first_try_pass_rate']:.2f} pass={group['pass_rate']:.2f}"
        )

    tested_models = {str(group.get("model")) for group in groups if group.get("model")}
    candidates = catalog_explore_candidates(catalog_models, tested_models=tested_models)
    print(f"CANDIDATES from {catalog_path}")
    if not candidates:
        print("  no untested text->text candidates with context >= 32000")
    for model in candidates:
        marker = " FREE" if model.get("free") else ""
        print(
            f"  untested  {model['id']} "
            f"in={format_catalog_price(model.get('prompt_per_m'))}/M "
            f"out={format_catalog_price(model.get('completion_per_m'))}/M "
            f"ctx={int(model.get('context_length') or 0)}{marker}"
        )


def active_runs_path() -> Path:
    return ringer_home() / "active-runs.json"


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_active_runs_raw(path: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    runs: dict[str, dict[str, Any]] = {}
    for run_id, value in data.items():
        if isinstance(run_id, str) and isinstance(value, dict):
            runs[run_id] = value
    return runs


def _prune_active_runs(runs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    pruned: dict[str, dict[str, Any]] = {}
    for run_id, entry in runs.items():
        pid = entry.get("pid")
        if isinstance(pid, bool):
            continue
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        if not pid_is_alive(pid_int):
            continue
        pruned[run_id] = {
            "pid": pid_int,
            "identity": str(entry.get("identity", "")),
            "run_name": str(entry.get("run_name", "")),
            "workdir": str(entry.get("workdir", "")),
            "started_at": str(entry.get("started_at", "")),
        }
    return pruned


def _write_active_runs(runs: dict[str, dict[str, Any]]) -> None:
    path = active_runs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(_prune_active_runs(runs), indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def read_active_runs() -> dict[str, dict[str, Any]]:
    path = active_runs_path()
    runs = _read_active_runs_raw(path)
    pruned = _prune_active_runs(runs)
    if pruned != runs:
        _write_active_runs(pruned)
    return pruned


def register_active_run(
    run_id: str,
    identity: str,
    run_name: str,
    workdir: Path,
    *,
    pid: int | None = None,
    started_at: datetime | None = None,
) -> None:
    runs = read_active_runs()
    runs[run_id] = {
        "pid": int(pid if pid is not None else os.getpid()),
        "identity": identity,
        "run_name": run_name,
        "workdir": str(workdir),
        "started_at": (started_at or datetime.now(timezone.utc)).isoformat(),
    }
    _write_active_runs(runs)


def unregister_active_run(run_id: str) -> None:
    runs = read_active_runs()
    runs.pop(run_id, None)
    _write_active_runs(runs)


def artifacts_dir(state_dir: Path) -> Path:
    return state_dir / "artifacts"


def artifact_library_path(state_dir: Path) -> Path:
    return artifacts_dir(state_dir) / "library.json"


def artifact_live_path(state_dir: Path, run_name: str) -> Path:
    return artifacts_dir(state_dir) / "live" / f"{sanitize_artifact_name(run_name)}.html"


def artifact_version_path(state_dir: Path, run_name: str, run_id: str) -> Path:
    return (
        artifacts_dir(state_dir)
        / "versions"
        / sanitize_artifact_name(run_name)
        / f"{sanitize_artifact_name(run_id)}.html"
    )


def artifact_deliverables_dir(state_dir: Path, run_id: str, task_key: str) -> Path:
    return (
        artifacts_dir(state_dir)
        / "deliverables"
        / sanitize_artifact_name(run_id)
        / sanitize_artifact_name(task_key)
    )


def read_artifact_library(state_dir: Path) -> dict[str, Any]:
    path = artifact_library_path(state_dir)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {"artifacts": {}}
    if not isinstance(data, dict):
        return {"artifacts": {}}
    artifacts = data.get("artifacts")
    if not isinstance(artifacts, dict):
        return {"artifacts": {}}
    clean: dict[str, Any] = {"artifacts": {}}
    for run_name, entry in artifacts.items():
        if isinstance(run_name, str) and isinstance(entry, dict):
            clean["artifacts"][run_name] = entry
    return clean


def write_artifact_library(state_dir: Path, library: dict[str, Any]) -> None:
    atomic_write_json(artifact_library_path(state_dir), library)


def artifact_outcome_from_state(state: dict[str, Any]) -> str:
    if str(state.get("state", "")) == "died":
        return "died"
    if not bool(state.get("finished")) and str(state.get("state", "live")) == "live":
        return "live"
    totals = state.get("totals") if isinstance(state.get("totals"), dict) else {}
    fail_n = int(totals.get("fail", state.get("fail", 0)) or 0)
    return "fail" if fail_n else "pass"


def _library_entry(
    *,
    state_dir: Path,
    run_name: str,
    run_id: str,
    identity: str,
    state: str,
    now_iso: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    versions = []
    if existing and isinstance(existing.get("versions"), list):
        versions = [item for item in existing["versions"] if isinstance(item, dict)]
    return {
        "live_path": str(artifact_live_path(state_dir, run_name)),
        "state": state,
        "identity": identity,
        "current_run_id": run_id,
        "updated_at": now_iso,
        "versions": versions,
    }


def update_artifact_library_live(
    state_dir: Path,
    *,
    run_name: str,
    run_id: str,
    identity: str,
    state: str,
    now: datetime | None = None,
) -> None:
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    library = read_artifact_library(state_dir)
    artifacts = library.setdefault("artifacts", {})
    existing = artifacts.get(run_name) if isinstance(artifacts.get(run_name), dict) else None
    artifacts[run_name] = _library_entry(
        state_dir=state_dir,
        run_name=run_name,
        run_id=run_id,
        identity=identity,
        state=state,
        now_iso=now_iso,
        existing=existing,
    )
    write_artifact_library(state_dir, library)


def append_artifact_library_version(
    state_dir: Path,
    *,
    run_name: str,
    run_id: str,
    identity: str,
    outcome: str,
    version_path: Path,
    report_path: Path | None,
    tasks_pass: int,
    tasks_fail: int,
    deliverables: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> None:
    now_iso = (now or datetime.now(timezone.utc)).isoformat()
    library = read_artifact_library(state_dir)
    artifacts = library.setdefault("artifacts", {})
    existing = artifacts.get(run_name) if isinstance(artifacts.get(run_name), dict) else None
    entry = _library_entry(
        state_dir=state_dir,
        run_name=run_name,
        run_id=run_id,
        identity=identity,
        state=outcome,
        now_iso=now_iso,
        existing=existing,
    )
    new_version = {
        "run_id": run_id,
        "path": str(version_path),
        "report_path": str(report_path) if report_path is not None else None,
        "finished_at": now_iso,
        "outcome": outcome,
        "tasks_pass": tasks_pass,
        "tasks_fail": tasks_fail,
        "deliverables": [dict(item) for item in deliverables or []],
    }
    versions = [new_version]
    for version in entry["versions"]:
        if version.get("run_id") != run_id:
            versions.append(version)
    entry["versions"] = versions[:ARTIFACT_LIBRARY_MAX_VERSIONS]
    artifacts[run_name] = entry
    write_artifact_library(state_dir, library)
    prune_artifact_versions(state_dir, versions[ARTIFACT_LIBRARY_MAX_VERSIONS:])


def prune_artifact_versions(state_dir: Path, versions: list[dict[str, Any]]) -> None:
    root = artifacts_dir(state_dir).resolve()
    for version in versions:
        for key in ("path", "report_path"):
            raw = version.get(key)
            if not raw:
                continue
            path = Path(str(raw)).expanduser()
            with contextlib.suppress(OSError):
                resolved = path.resolve()
                if resolved == root or root not in resolved.parents:
                    continue
                if resolved.is_file():
                    resolved.unlink()
                    with contextlib.suppress(OSError):
                        resolved.parent.rmdir()


def reconcile_artifact_library_dead_runs(state_dir: Path) -> None:
    library = read_artifact_library(state_dir)
    artifacts = library.get("artifacts", {})
    if not isinstance(artifacts, dict):
        return
    active = read_active_runs()
    changed = False
    now_iso = datetime.now(timezone.utc).isoformat()
    for entry in artifacts.values():
        if not isinstance(entry, dict) or entry.get("state") != "live":
            continue
        run_id = str(entry.get("current_run_id", ""))
        if not run_id or run_id not in active:
            entry["state"] = "died"
            entry["updated_at"] = now_iso
            changed = True
    if changed:
        write_artifact_library(state_dir, library)


def scan_run_states(state_dir: Path) -> list[dict[str, Any]]:
    """Best-effort scan of every run state file, for the multi-run index artifact."""
    runs_dir = state_dir / "runs"
    entries: list[dict[str, Any]] = []
    try:
        paths = list(runs_dir.glob("*.json"))
    except OSError:
        return entries
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        data = reconcile_dead_run_state(data)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        entries.append(
            {
                "run_id": data.get("run_id", path.stem),
                "run_name": data.get("run_name", "ringer"),
                "identity": data.get("identity", "unknown"),
                "state": data.get("state", "finished" if data.get("finished") else "live"),
                "pass": data.get("pass", 0),
                "fail": data.get("fail", 0),
                "elapsed_s": data.get("elapsed_s", 0),
                "started_at": data.get("started_at", ""),
                "artifact_path": data.get("artifact_path"),
                "report_path": data.get("report_path"),
                "report_ready": data.get("report_ready", False),
                "mtime": mtime,
            }
        )
    entries.sort(key=lambda item: item["mtime"], reverse=True)
    return entries


STATUS_COLORS = {
    "pass": "var(--pass)",
    "fail": "var(--fail)",
    "error": "var(--fail)",
    "timeout": "var(--fail)",
    "running": "var(--running)",
    "retrying": "var(--running)",
    "verifying": "var(--running)",
    "queued": "var(--waiting)",
    "died": "var(--fail)",
    "live": "var(--running)",
    "finished": "var(--pass)",
}


def status_color(status: str) -> str:
    return STATUS_COLORS.get(str(status).lower(), "var(--waiting)")


def fmt_duration(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def fmt_datetime(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_compact_duration(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    parts: list[str] = []
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    if s or not parts:
        parts.append(f"{s}s")
    return " ".join(parts)


def fmt_plain_ago(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds or 0)))
    except (TypeError, ValueError):
        total = 0
    if total < 60:
        return f"{total} second{'s' if total != 1 else ''}"
    minutes, seconds_left = divmod(total, 60)
    if minutes < 60:
        if seconds_left == 0:
            return f"{minutes} minute{'s' if minutes != 1 else ''}"
        return (
            f"{minutes} minute{'s' if minutes != 1 else ''} "
            f"{seconds_left} second{'s' if seconds_left != 1 else ''}"
        )
    hours, minutes_left = divmod(minutes, 60)
    if minutes_left == 0:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return (
        f"{hours} hour{'s' if hours != 1 else ''} "
        f"{minutes_left} minute{'s' if minutes_left != 1 else ''}"
    )


ARTIFACT_BASE_CSS = """
  :root {
    color-scheme: dark;
    --ground: #0b0e14;
    --surface: #141a26;
    --ink: #e9eef7;
    --muted: #8fa0b6;
    --hairline: rgba(143, 160, 182, .22);
    --accent: #35d0ff;
    --pass: #45d17e;
    --fail: #ff5f6b;
    --waiting: #6f7c92;
    --quote-bg: rgba(255, 95, 107, .08);
  }
  @media (prefers-color-scheme: light) {
    :root {
      color-scheme: light;
      --ground: #f2f5f9;
      --surface: #ffffff;
      --ink: #17202e;
      --muted: #5a6a7e;
      --hairline: rgba(90, 106, 126, .28);
      --accent: #007fb0;
      --pass: #178a4c;
      --fail: #cc3340;
      --waiting: #7d8ba0;
      --quote-bg: rgba(204, 51, 64, .07);
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --ground: #0b0e14; --surface: #141a26; --ink: #e9eef7; --muted: #8fa0b6;
    --hairline: rgba(143,160,182,.22); --accent: #35d0ff; --pass: #45d17e;
    --fail: #ff5f6b; --waiting: #6f7c92; --quote-bg: rgba(255,95,107,.08);
  }
  :root[data-theme="light"] {
    color-scheme: light;
    --ground: #f2f5f9; --surface: #ffffff; --ink: #17202e; --muted: #5a6a7e;
    --hairline: rgba(90,106,126,.28); --accent: #007fb0; --pass: #178a4c;
    --fail: #cc3340; --waiting: #7d8ba0; --quote-bg: rgba(204,51,64,.07);
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0;
    min-height: 100%;
    overflow-x: hidden;
    background: var(--ground);
    color: var(--ink);
    font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    line-height: 1.5;
  }
  body {
    padding: clamp(18px, 4vw, 52px);
  }
  .page {
    max-width: 860px;
    margin: 0 auto;
  }
  .corner {
    display: flex;
    align-items: baseline;
    gap: 12px;
    flex-wrap: wrap;
    margin-bottom: clamp(14px, 3vw, 26px);
  }
  .live-dot {
    width: 9px;
    height: 9px;
    border-radius: 50%;
    background: var(--accent);
    align-self: center;
    flex: 0 0 9px;
  }
  .live-dot.pass { background: var(--pass); }
  .live-dot.fail, .live-dot.retry { background: var(--fail); }
  .live-dot.waiting { background: var(--waiting); }
  @media (prefers-reduced-motion: no-preference) {
    .live-dot.is-live { animation: pulse 1.4s ease-in-out infinite; }
    @keyframes pulse { 50% { opacity: .35; } }
  }
  .eyebrow {
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .12em;
    text-transform: uppercase;
  }
  .eyebrow b {
    color: var(--ink);
  }
  .clock {
    margin-left: auto;
    color: var(--muted);
    font-size: 12px;
  }
  .briefing {
    max-width: 30ch;
    margin: 0 0 clamp(16px, 3vw, 24px);
    font-size: clamp(20px, 3.4vw, 30px);
    font-weight: 800;
    letter-spacing: 0;
    line-height: 1.25;
    text-wrap: balance;
  }
  .briefing .n-pass { color: var(--pass); }
  .briefing .n-fail { color: var(--fail); }
  .rounds {
    display: flex;
    gap: 5px;
    margin-bottom: 8px;
  }
  .rounds span {
    flex: 1;
    height: 7px;
    border-radius: 4px;
    background: var(--waiting);
    opacity: .45;
  }
  .rounds .pass { background: var(--pass); opacity: 1; }
  .rounds .working { background: var(--accent); opacity: 1; }
  .rounds .retry, .rounds .fail { background: var(--fail); opacity: 1; }
  @media (prefers-reduced-motion: no-preference) {
    .rounds .working, .rounds .retry { animation: pulse 1.4s ease-in-out infinite; }
  }
  .legend {
    margin: 0;
    margin-bottom: clamp(26px, 5vw, 40px);
    color: var(--muted);
    font-size: 12.5px;
  }
  .work {
    margin-bottom: clamp(28px, 5vw, 44px);
  }
  .work-list {
    display: grid;
    gap: 10px;
    margin-top: 10px;
  }
  .work-item {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 12px 0;
    border-bottom: 1px solid var(--hairline);
  }
  .work-main {
    min-width: 0;
  }
  .work-link {
    color: var(--ink);
    font-size: 15px;
    font-weight: 750;
  }
  .work-kind {
    margin-top: 2px;
    color: var(--muted);
    font-size: 12.5px;
  }
  .work-task {
    display: inline-block;
    margin-left: 6px;
  }
  .work-thumb-link {
    flex: 0 0 auto;
  }
  .work-thumb {
    display: block;
    max-width: 132px;
    max-height: 96px;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    object-fit: cover;
  }
  .work.is-primary .work-list {
    gap: 12px;
  }
  .work.is-primary .work-link {
    font-size: clamp(17px, 2.6vw, 22px);
  }
  .work-group .worker {
    border-bottom: none;
    padding-bottom: 6px;
  }
  .work-group-body {
    padding: 0 0 14px 30px;
    border-bottom: 1px solid var(--hairline);
  }
  .work-group:last-child .work-group-body { border-bottom: none; }
  .work-group-body .work-item:last-of-type { border-bottom: none; }
  .work-group-body .empty-note { margin: 4px 0 8px; }
  .work-group-body .verified {
    display: block;
    margin-top: 6px;
    color: var(--muted);
    font-size: 13px;
    overflow-wrap: break-word;
  }
  .work-group-body .proof {
    margin-top: 4px;
    font-size: 12px;
  }
  .work-group-body .proof summary {
    cursor: pointer;
    color: var(--accent);
  }
  .work-group-body .proof pre {
    margin: 6px 0 0;
    padding: 10px 12px;
    max-height: 200px;
    overflow: auto;
    border-left: 2px solid var(--hairline);
    background: var(--surface);
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .work-group-body .links {
    display: block;
    margin-top: 8px;
    font-size: 13px;
  }
  .work-group-body .links a {
    color: var(--accent);
    text-decoration: none;
  }
  .work-group-body .links a:hover,
  .work-group-body .links a:focus-visible {
    text-decoration: underline;
  }
  .work.is-primary .work-group {
    padding: 12px 16px 2px;
    border: 1px solid var(--hairline);
    border-radius: 8px;
    background: var(--surface);
  }
  .work.is-primary .work-group-body { border-bottom: none; }
  section h2 {
    margin: 0 0 4px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--hairline);
    color: var(--muted);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: .1em;
    text-transform: uppercase;
  }
  .timeline {
    margin-bottom: clamp(28px, 5vw, 44px);
  }
  details.timeline > summary {
    cursor: pointer;
    list-style: none;
  }
  details.timeline > summary::-webkit-details-marker { display: none; }
  details.timeline > summary h2::after {
    content: " ▸";
    color: var(--muted);
    font-size: 11px;
  }
  details.timeline[open] > summary h2::after { content: " ▾"; }
  details.timeline > summary:focus-visible {
    outline: 2px solid var(--accent);
    outline-offset: 2px;
  }
  .tl-row {
    display: grid;
    grid-template-columns: 76px minmax(0,1fr);
    gap: 14px;
    padding: 10px 0;
    border-bottom: 1px solid var(--hairline);
    font-size: 14px;
  }
  .tl-row time {
    color: var(--muted);
    font-size: 12px;
    padding-top: 2px;
  }
  .tl-row .catch {
    margin: 6px 0 0;
    padding: 8px 12px;
    background: var(--quote-bg);
    border-left: 2px solid var(--fail);
    border-radius: 0 6px 6px 0;
    color: var(--muted);
    font-size: 13px;
    overflow-wrap: break-word;
  }
  .tl-row .catch b {
    color: var(--fail);
    font-weight: 650;
  }
  .workers {
    margin-bottom: clamp(28px, 5vw, 44px);
  }
  .worker {
    display: grid;
    grid-template-columns: 18px minmax(0,1fr) auto auto;
    gap: 4px 12px;
    align-items: baseline;
    padding: 12px 0;
    border-bottom: 1px solid var(--hairline);
  }
  .glyph {
    width: 11px;
    height: 11px;
    border-radius: 50%;
    align-self: center;
  }
  .glyph.pass { background: var(--pass); }
  .glyph.working { background: var(--accent); }
  .glyph.retry, .glyph.fail { background: var(--fail); }
  .glyph.waiting {
    background: transparent;
    border: 1.5px solid var(--waiting);
  }
  @media (prefers-reduced-motion: no-preference) {
    .glyph.working, .glyph.retry { animation: pulse 1.4s ease-in-out infinite; }
  }
  .worker .name {
    min-width: 0;
    overflow: hidden;
    font-size: 15px;
    font-weight: 650;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .worker .state {
    font-size: 13px;
    font-weight: 650;
    white-space: nowrap;
  }
  .state.pass { color: var(--pass); }
  .state.working { color: var(--accent); }
  .state.retry, .state.fail { color: var(--fail); }
  .state.waiting { color: var(--waiting); }
  .worker .time {
    color: var(--muted);
    font-size: 12.5px;
    white-space: nowrap;
  }
  .worker .activity {
    grid-column: 2 / -1;
    min-width: 0;
    overflow: hidden;
    color: var(--muted);
    font-size: 13px;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .worker .verified {
    grid-column: 2 / -1;
    min-width: 0;
    color: var(--muted);
    font-size: 13px;
    overflow-wrap: break-word;
  }
  .worker .proof {
    grid-column: 2 / -1;
    min-width: 0;
    font-size: 12px;
  }
  .worker .proof summary {
    cursor: pointer;
    color: var(--accent);
  }
  .worker .proof pre {
    margin: 6px 0 0;
    padding: 10px 12px;
    max-height: 200px;
    overflow: auto;
    border-left: 2px solid var(--hairline);
    background: var(--surface);
    color: var(--muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 11.5px;
    line-height: 1.55;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .worker .links {
    grid-column: 2 / -1;
    font-size: 13px;
  }
  .worker .links a {
    color: var(--accent);
    text-decoration: none;
  }
  .worker .links a:hover,
  .worker .links a:focus-visible {
    text-decoration: underline;
  }
  .runs {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .omitted-note,
  .empty-note {
    max-width: 65ch;
    margin: 8px 0 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.45;
  }
  .run-row {
    display: grid;
    gap: 16px;
    align-items: center;
    padding: 12px 0;
    border-top: 1px solid var(--hairline);
  }
  .run-row {
    grid-template-columns: minmax(0, 1.35fr) minmax(112px, .55fr) minmax(76px, .4fr) minmax(150px, .8fr);
  }
  .run-name {
    min-width: 0;
    overflow: hidden;
    color: var(--ink);
    font-weight: 700;
    line-height: 1.35;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .run-state {
    color: var(--state-color);
    font-weight: 800;
  }
  .run-duration {
    color: var(--muted);
  }
  .run-links {
    display: flex;
    min-width: 0;
    flex-wrap: wrap;
    gap: 8px 14px;
  }
  .run-links .muted {
    color: var(--muted);
  }
  .state-pass { --state-color: var(--pass); }
  .state-fail { --state-color: var(--fail); }
  .state-running { --state-color: var(--accent); }
  .state-waiting { --state-color: var(--waiting); }
  .meta {
    max-width: 65ch;
    margin: 0 0 18px;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.55;
  }
  .meta b { color: var(--ink); }
  .mono,
  time {
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-variant-numeric: tabular-nums;
  }
  .muted { color: var(--muted); }
  table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
  th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid var(--hairline); vertical-align: top; }
  th { color: var(--muted); font-weight: 700; font-size: 10px; letter-spacing: 0; }
  .chip { display: inline-block; padding: 2px 8px; border-radius: 6px; font-size: 10px; font-weight: 800; color: var(--ground); white-space: nowrap; }
  pre {
    width: 100%;
    max-width: 100%;
    margin: 0;
    overflow: auto;
    border: 1px solid var(--hairline);
    border-radius: 6px;
    background: var(--surface);
    color: var(--ink);
    padding: clamp(14px,3vw,24px);
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    line-height: 1.65;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  footer,
  .page-foot {
    display: flex;
    gap: 8px;
    flex-wrap: wrap;
    color: var(--muted);
    font-size: 12px;
    line-height: 1.5;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  @media (max-width: 640px) {
    .worker,
    .run-row {
      grid-template-columns: minmax(0, 1fr);
      gap: 6px;
    }
    .glyph {
      display: none;
    }
    .worker .activity,
    .worker .links {
      grid-column: 1 / -1;
    }
    .work-item,
    .work.is-primary .work-item {
      align-items: flex-start;
      padding: 12px 0;
      border-width: 0 0 1px;
      border-radius: 0;
      background: transparent;
    }
    .work-thumb {
      max-width: 96px;
      max-height: 72px;
    }
    .run-links {
      gap: 6px 12px;
    }
  }
"""


def file_href(path: Path) -> str:
    try:
        return path.resolve().as_uri()
    except ValueError:
        return "file://" + urllib.parse.quote(str(path))


def sanitize_artifact_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip(".-")
    return sanitized or "artifact"


def is_html_artifact(path: Path) -> bool:
    return path.suffix.lower() in {".html", ".htm"}


def deliverable_title(path: Path) -> str:
    name = path.name.lower()
    if name == "worker.log":
        return "Work log"
    if name in TASK_REPORT_FILENAMES:
        return "What this worker produced"
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    return stem.capitalize() if stem else "Worker output"


class ArtifactRenderer:
    def __init__(self, artifact_path: Path) -> None:
        self.artifact_dir = artifact_path.parent
        self._wrapper_cache: dict[tuple[Path, Path], tuple[int, int]] = {}
        self._last_task_status: dict[str, str] = {}
        self._last_run_state: str | None = None
        self._seen_transition_keys: set[tuple[str, str]] = set()
        self._transition_log: list[dict[str, str]] = []

    def render_status_html(self, state: dict[str, Any], *, page_path: Path | None = None) -> str:
        return render_status_html(state, renderer=self, force_wrappers=False, page_path=page_path)

    def render_final_report_html(self, state: dict[str, Any], *, page_path: Path | None = None) -> str:
        return render_final_report_html(state, renderer=self, force_wrappers=True, page_path=page_path)

    def render_artifact_index_html(self, entries: list[dict[str, Any]]) -> str:
        return render_artifact_index_html(entries, renderer=self, force_wrappers=False)

    def transition_feed(self, state: dict[str, Any], *, limit: int | None = None) -> list[dict[str, str]]:
        self.record_transitions(state)
        if limit is None:
            return list(reversed(self._transition_log))
        return list(reversed(self._transition_log[-limit:]))

    def omitted_transition_count(self, limit: int) -> int:
        return max(0, len(self._transition_log) - limit)

    def record_transitions(self, state: dict[str, Any]) -> None:
        run_state = str(state.get("state", "live"))
        if self._last_run_state is None:
            if run_state == "live":
                self._append_transition(("run", "live"), "Ringer started")
        elif self._last_run_state != run_state and run_state == "finished":
            self._append_transition(("run", "finished"), "Ringer finished")
        self._last_run_state = run_state

        current_status: dict[str, str] = {}
        tasks = state.get("tasks") or []
        if not isinstance(tasks, list):
            tasks = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_key = str(task.get("key", "task"))
            status = str(task.get("status", "queued"))
            previous = self._last_task_status.get(task_key)
            current_status[task_key] = status
            if previous == status:
                continue
            event = plain_transition_event(task_key, previous, status, task)
            if event:
                self._append_transition((task_key, status), event)
        self._last_task_status = current_status

    def _append_transition(self, key: tuple[str, str], event: str | dict[str, str]) -> None:
        if key in self._seen_transition_keys:
            return
        self._seen_transition_keys.add(key)
        if isinstance(event, str):
            event = {"line": event}
        self._transition_log.append({"time": datetime.now().strftime("%H:%M:%S"), **event})

    def link_for_source(
        self,
        source_path: Path,
        *,
        state: dict[str, Any] | None = None,
        run_id: str | None = None,
        run_name: str | None = None,
        task_key: str,
        force: bool = False,
    ) -> str:
        if is_html_artifact(source_path):
            return file_href(source_path)
        if not source_path.exists():
            return file_href(source_path)

        wrapper_path = self.wrapper_path(
            run_id=str(run_id or (state or {}).get("run_id") or "run"),
            task_key=task_key,
            source_name=source_path.name,
        )
        self.write_wrapper(
            source_path,
            wrapper_path,
            run_name=str(run_name or (state or {}).get("run_name") or "ringer"),
            task_key=task_key,
            force=force,
        )
        return file_href(wrapper_path)

    def wrapper_path(self, *, run_id: str, task_key: str, source_name: str) -> Path:
        filename = f"{sanitize_artifact_name(task_key)}--{sanitize_artifact_name(source_name)}.html"
        return self.artifact_dir / "view" / sanitize_artifact_name(run_id) / filename

    def write_wrapper(
        self,
        source_path: Path,
        wrapper_path: Path,
        *,
        run_name: str,
        task_key: str,
        force: bool = False,
    ) -> None:
        stat = source_path.stat()
        cache_key = (source_path.resolve(), wrapper_path)
        current = (stat.st_mtime_ns, stat.st_size)
        if not force and wrapper_path.exists() and self._wrapper_cache.get(cache_key) == current:
            return

        html = render_file_wrapper_html(
            source_path=source_path,
            source_stat=stat,
            run_name=run_name,
            task_key=task_key,
        )
        atomic_write_text(wrapper_path, html)
        self._wrapper_cache[cache_key] = current


def render_file_wrapper_html(
    *,
    source_path: Path,
    source_stat: os.stat_result,
    run_name: str,
    task_key: str,
) -> str:
    size = int(source_stat.st_size)
    truncated = size > ARTIFACT_WRAPPER_TAIL_BYTES
    start = max(0, size - ARTIFACT_WRAPPER_TAIL_BYTES)
    with source_path.open("rb") as fh:
        if start:
            fh.seek(start)
        raw = fh.read()
    content = raw.decode("utf-8", errors="replace")
    source_mtime = datetime.fromtimestamp(source_stat.st_mtime).astimezone().strftime(
        "%Y-%m-%d %H:%M:%S %Z"
    )
    truncation_note = (
        f" Showing the last <b>{ARTIFACT_WRAPPER_TAIL_BYTES:,}</b> bytes"
        f" of <b>{size:,}</b>."
        if truncated
        else ""
    )
    title = html_escape(deliverable_title(source_path))
    safe_run_name = html_escape(run_name)
    safe_task_key = html_escape(task_key)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{CSP_META_TAG}
<title>{title}</title>
<style>{ARTIFACT_BASE_CSS}</style>
</head>
<body>
<div class="page">
  <header class="corner">
    <span class="live-dot waiting" aria-hidden="true"></span>
    <span class="eyebrow">Ringer &nbsp;·&nbsp; <b>{safe_run_name}</b> &nbsp;·&nbsp; {safe_task_key}</span>
    <span class="clock mono">artifact</span>
  </header>
  <section class="timeline" aria-label="{title}">
    <h1 class="briefing">{title}</h1>
    <p class="meta">{safe_task_key} produced this on <b>{source_mtime}</b>.{truncation_note}</p>
  </section>
  <pre>{html_escape(content)}</pre>
</div>
</body>
</html>
"""


def state_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    tasks = state.get("tasks") or []
    if not isinstance(tasks, list):
        return []
    return [task for task in tasks if isinstance(task, dict)]


def collect_state_deliverables(state: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for task in state_tasks(state):
        task_key = str(task.get("key", "task"))
        deliverables = task.get("deliverables") or []
        if not isinstance(deliverables, list):
            continue
        for item in deliverables:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip()
            if not name or not path:
                continue
            try:
                size = int(item.get("bytes", 0) or 0)
            except (TypeError, ValueError):
                size = 0
            items.append(
                {
                    "task_key": task_key,
                    "name": name,
                    "path": path,
                    "bytes": size,
                }
            )
    return items


def task_status_counts(state: dict[str, Any]) -> dict[str, int]:
    tasks = state_tasks(state)
    buckets = [task_state_bucket(str(task.get("status", "queued"))) for task in tasks]
    pass_n = sum(1 for bucket in buckets if bucket == "pass")
    fail_n = sum(1 for bucket in buckets if bucket == "fail")
    running_n = sum(1 for bucket in buckets if bucket == "working")
    retry_n = sum(1 for bucket in buckets if bucket == "retry")
    waiting_n = sum(1 for bucket in buckets if bucket == "waiting")
    return {
        "total": len(tasks),
        "pass": pass_n,
        "fail": fail_n,
        "running": running_n,
        "retry": retry_n,
        "waiting": waiting_n,
    }


def task_word(count: int) -> str:
    return "task" if count == 1 else "tasks"


def passed_phrase(count: int) -> str:
    if count == 1:
        return "1 finished and checked"
    return f"{count} finished and checked"


def failed_phrase(count: int) -> str:
    if count == 1:
        return "1 failed"
    return f"{count} failed"


def running_phrase(count: int) -> str:
    if count == 1:
        return "1 working"
    return f"{count} working"


def retry_phrase(count: int) -> str:
    if count == 1:
        return "1 sent back"
    return f"{count} sent back"


def waiting_phrase(count: int) -> str:
    if count == 1:
        return "1 is waiting"
    return f"{count} are waiting"


def live_briefing_sentence(state: dict[str, Any]) -> str:
    return html_to_text(live_briefing_html(state))


def live_briefing_html(state: dict[str, Any]) -> str:
    counts = task_status_counts(state)
    elapsed = fmt_plain_ago(state.get("elapsed_s"))
    total = counts["total"]
    if total == 0:
        return f"Ringer has no tasks. Started {html_escape(elapsed)} ago."
    parts = []
    if counts["pass"]:
        parts.append(f'<span class="n-pass">{html_escape(passed_phrase(counts["pass"]))}</span>')
    if counts["running"]:
        parts.append(html_escape(running_phrase(counts["running"])))
    if counts["retry"]:
        parts.append(f'<span class="n-fail">{html_escape(retry_phrase(counts["retry"]))}</span>')
    if counts["waiting"]:
        parts.append(html_escape(waiting_phrase(counts["waiting"])))
    if counts["fail"]:
        parts.append(f'<span class="n-fail">{html_escape(failed_phrase(counts["fail"]))}</span>')
    status_sentence = join_plain_html_parts(parts)
    return (
        f"Ringer is working on {total} {task_word(total)} — "
        f"{status_sentence}, started {html_escape(elapsed)} ago."
    )


def final_briefing_sentence(state: dict[str, Any]) -> str:
    return html_to_text(final_briefing_html(state))


def final_briefing_html(state: dict[str, Any]) -> str:
    counts = task_status_counts(state)
    total = counts["total"]
    pass_n = counts["pass"]
    fail_n = counts["fail"]
    elapsed = fmt_compact_duration(state.get("elapsed_s"))
    first = f"Ringer finished {total} {task_word(total)} in {elapsed}."
    if fail_n == 0:
        return f"{html_escape(first)} <span class=\"n-pass\">All {total} finished and checked.</span>"
    return (
        f"{html_escape(first)} <span class=\"n-pass\">{pass_n} finished and checked</span>, "
        f"<span class=\"n-fail\">{fail_n} failed after retry.</span>"
    )


def join_plain_html_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def html_to_text(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value)


def plain_transition_line(
    task_key: str,
    previous_status: str | None,
    status: str,
    task: dict[str, Any],
) -> str | None:
    event = plain_transition_event(task_key, previous_status, status, task)
    if not event:
        return None
    return event["line"]


def plain_transition_event(
    task_key: str,
    previous_status: str | None,
    status: str,
    task: dict[str, Any],
) -> dict[str, str] | None:
    attempts = int(task.get("attempts") or 0)
    timed_out = bool(task.get("check_timed_out")) or status == "timeout"
    check_excerpt = first_check_output_line(task)
    if status == "running" and previous_status in {None, "queued"}:
        return {"line": f"{task_key} started"}
    if status == "retrying":
        if timed_out:
            return {"line": f"{task_key} timed out — trying again"}
        if check_excerpt:
            return {
                "line": f"{task_key} didn't finish cleanly — sent back to redo the work.",
                "catch": check_excerpt,
            }
        return {"line": f"{task_key} did not finish cleanly — trying again"}
    if status == "pass":
        if attempts > 1:
            return {"line": f"{task_key} passed on the second try, {fmt_compact_duration(task.get('elapsed_s'))}"}
        return {"line": f"{task_key} finished and checked, {fmt_compact_duration(task.get('elapsed_s'))}"}
    if status == "fail":
        if timed_out:
            return {"line": f"{task_key} timed out"}
        if check_excerpt:
            return {"line": f"{task_key} could not finish.", "catch": check_excerpt}
        if attempts > 1:
            return {"line": f"{task_key} failed after the second try"}
        return {"line": f"{task_key} failed"}
    if status == "timeout":
        return {"line": f"{task_key} timed out"}
    return None


def first_check_output_line(task: dict[str, Any]) -> str:
    raw = task.get("check_output_tail") or task.get("check_output") or ""
    for line in str(raw).splitlines():
        clean = line.strip()
        if clean:
            return shorten(clean, 120)
    return ""


def task_state_bucket(status: str) -> str:
    status = str(status).lower()
    if status == "pass":
        return "pass"
    if status in {"fail", "error", "timeout", "died"}:
        return "fail"
    if status == "retrying":
        return "retry"
    if status in {"running", "verifying"}:
        return "working"
    return "waiting"


def task_state_word(status: str) -> str:
    bucket = task_state_bucket(status)
    if bucket == "pass":
        return "finished & checked"
    if bucket == "working":
        return "working"
    if bucket == "retry":
        return "sent back — redoing"
    if bucket == "fail":
        return "failed"
    return "waiting"


def local_time_label() -> str:
    return datetime.now().astimezone().strftime("%H:%M:%S %Z")


def render_progress_bar(tasks: list[dict[str, Any]], counts: dict[str, int]) -> str:
    segments = []
    for task in tasks:
        key = html_escape(str(task.get("key", "task")))
        bucket = task_state_bucket(str(task.get("status", "queued")))
        state_word = html_escape(task_state_word(str(task.get("status", "queued"))))
        css_class = "" if bucket == "waiting" else f' class="{bucket}"'
        segments.append(
            f'<span{css_class} aria-label="{key}: {state_word}"></span>'
        )
    bar = "".join(segments) if segments else ""
    legend_parts = []
    if counts["pass"]:
        legend_parts.append(f'{counts["pass"]} finished')
    if counts["running"]:
        legend_parts.append(f'{counts["running"]} working')
    if counts["retry"]:
        legend_parts.append(f'{counts["retry"]} sent back')
    if counts["fail"]:
        legend_parts.append(f'{counts["fail"]} failed')
    if counts["waiting"]:
        legend_parts.append(f'{counts["waiting"]} waiting')
    legend = " · ".join(legend_parts) if legend_parts else "No tasks"
    aria = (
        f'{counts["total"]} tasks: {counts["pass"]} passed, {counts["running"]} working, '
        f'{counts["retry"]} retrying, {counts["waiting"]} waiting, {counts["fail"]} failed'
    )
    return f"""<div class="rounds" role="img" aria-label="{html_escape(aria)}">{bar}</div>
    <p class="legend">{html_escape(legend)}</p>"""


def render_work_section(
    state: dict[str, Any],
    *,
    renderer: ArtifactRenderer | None,
    page_path: Path | None,
    force_wrappers: bool = False,
    primary: bool = False,
    finished_only: bool = False,
) -> str:
    # One section carries the whole story: each worker, what it delivered,
    # how the delivery was checked, and where the raw log lives. The old
    # separate "The workers" strip and "What's happening" timeline repeated
    # this information; per-worker live detail belongs to Ringside's agent
    # accordion, not the artifact.
    tasks = state_tasks(state)
    if finished_only:
        tasks = [
            task
            for task in tasks
            if task_state_bucket(str(task.get("status", "queued"))) in {"pass", "fail"}
        ]
    section_class = "work is-primary" if primary else "work"
    if not tasks:
        empty_note = "Deliverables appear here as workers finish." if finished_only else "No tasks."
        body = f'<p class="empty-note">{empty_note}</p>'
    else:
        groups = "".join(
            render_work_group(
                task,
                state=state,
                renderer=renderer,
                page_path=page_path,
                force_wrappers=force_wrappers,
            )
            for task in tasks
        )
        body = f'<div class="work-list">{groups}</div>'
    return f"""<section class="{section_class}" aria-labelledby="the-work-heading">
    <h2 id="the-work-heading">The work</h2>
    {body}
  </section>"""


def render_work_group(
    task: dict[str, Any],
    *,
    state: dict[str, Any],
    renderer: ArtifactRenderer | None,
    page_path: Path | None,
    force_wrappers: bool = False,
) -> str:
    task_key = str(task.get("key", "task"))
    key = html_escape(task_key)
    status = str(task.get("status", "queued"))
    bucket = task_state_bucket(status)
    css_bucket = "working" if bucket == "working" else bucket
    state_word = html_escape(task_state_word(status))
    elapsed = html_escape(fmt_compact_duration(task.get("elapsed_s")))

    activity = task_activity_line(task, bucket)
    activity_html = (
        f'<span class="activity" title="{html_escape(activity)}">{html_escape(activity)}</span>'
        if activity
        else ""
    )

    deliverables = [item for item in (task.get("deliverables") or []) if isinstance(item, dict)]
    rows = [
        render_work_item(
            item,
            task_key=task_key,
            state=state,
            renderer=renderer,
            page_path=page_path,
            force_wrappers=force_wrappers,
        )
        for item in deliverables
    ]
    if rows:
        items_html = "".join(rows)
    elif bucket == "pass":
        items_html = '<p class="empty-note">Finished and checked — this worker filed nothing to the shelf.</p>'
    elif bucket == "fail":
        items_html = '<p class="empty-note">Failed its check — nothing was delivered.</p>'
    elif bucket in {"working", "retry"}:
        items_html = '<p class="empty-note">Nothing delivered yet — still on it.</p>'
    else:
        items_html = '<p class="empty-note">Waiting its turn.</p>'

    # Close the trust loop where the results live: say in plain English what
    # the check proved, and keep the raw evidence one click away. The proof
    # stands on its own — a failed task shows why it failed even when no
    # 'verified' sentence was written.
    verified_html = ""
    if bucket in {"pass", "fail"}:
        verified_text = str(task.get("verified") or "").strip()
        proof_tail = str(task.get("check_output_tail") or "").strip()
        if verified_text:
            how_label = "How it was checked" if bucket == "pass" else "What the check demanded"
            verified_html += f'<span class="verified">{how_label}: {html_escape(verified_text)}</span>'
        if proof_tail:
            proof_label = "See the proof" if bucket == "pass" else "See why it failed"
            verified_html += (
                f'<details class="proof"><summary>{proof_label}</summary>'
                f"<pre>{html_escape(shorten(proof_tail, 1200))}</pre></details>"
            )

    links_html = render_task_links(
        task,
        state=state,
        renderer=renderer,
        force_wrappers=force_wrappers,
        page_path=page_path,
    )

    return f"""<div class="work-group">
      <div class="worker">
        <span class="glyph {css_bucket}" aria-hidden="true"></span>
        <span class="name" title="{key}">{key}</span>
        <span class="state {css_bucket}">{state_word}</span>
        <span class="time mono">{elapsed}</span>
        {activity_html}
      </div>
      <div class="work-group-body">
        {items_html}
        {verified_html}
        <span class="links">{links_html}</span>
      </div>
    </div>"""


def render_work_item(
    item: dict[str, Any],
    *,
    task_key: str,
    state: dict[str, Any],
    renderer: ArtifactRenderer | None,
    page_path: Path | None,
    force_wrappers: bool = False,
) -> str:
    name = str(item.get("name", "")).strip() or "work"
    source_path = Path(str(item.get("path", "")))
    label, kind = work_label_and_kind(name)
    href = work_item_href(
        source_path,
        state=state,
        task_key=task_key,
        renderer=renderer,
        page_path=page_path,
        force_wrappers=force_wrappers,
    )
    thumb = ""
    if is_image_deliverable(source_path):
        thumb_src = image_data_uri(source_path)
        if thumb_src:
            thumb = (
                f'<a class="work-thumb-link" href="{html_escape(href)}">'
                f'<img class="work-thumb" src="{html_escape(thumb_src)}" alt=""></a>'
            )
    return f"""<div class="work-item">
      {thumb}
      <div class="work-main">
        <a class="work-link" href="{html_escape(href)}">{html_escape(label)}</a>
        <div class="work-kind">{html_escape(kind)}</div>
      </div>
    </div>"""


def work_item_href(
    source_path: Path,
    *,
    state: dict[str, Any],
    task_key: str,
    renderer: ArtifactRenderer | None,
    page_path: Path | None,
    force_wrappers: bool,
) -> str:
    if renderer is None:
        return "#"
    if is_text_deliverable(source_path) and source_path.exists():
        wrapper_path = renderer.wrapper_path(
            run_id=str(state.get("run_id") or "run"),
            task_key=task_key,
            source_name=source_path.name,
        )
        renderer.write_wrapper(
            source_path,
            wrapper_path,
            run_name=str(state.get("run_name") or "ringer"),
            task_key=task_key,
            force=force_wrappers,
        )
        return artifact_relative_href(
            wrapper_path,
            page_path=page_path,
            artifact_root=renderer.artifact_dir,
        )
    return artifact_relative_href(source_path, page_path=page_path, artifact_root=renderer.artifact_dir)


def artifact_relative_href(target: Path, *, page_path: Path | None, artifact_root: Path) -> str:
    try:
        root = artifact_root.resolve()
        resolved_target = target.resolve()
        if resolved_target != root and root not in resolved_target.parents:
            return "#"
        start = (page_path.parent if page_path is not None else artifact_root).resolve()
        rel = os.path.relpath(resolved_target, start)
    except (OSError, ValueError):
        return "#"
    return urllib.parse.quote(Path(rel).as_posix(), safe="/._-~")


def work_label_and_kind(name: str) -> tuple[str, str]:
    path = Path(name)
    stem = path.stem.replace("_", " ").replace("-", " ").strip()
    pretty = stem[:1].upper() + stem[1:] if stem else "Work"
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        kind = "web page"
    elif suffix in IMAGE_DELIVERABLE_SUFFIXES:
        kind = "image"
    elif suffix in TEXT_DELIVERABLE_SUFFIXES:
        kind = "document"
    else:
        kind = "download"
    return f"{pretty} — {kind}", kind


def is_text_deliverable(path: Path) -> bool:
    return path.suffix.lower() in TEXT_DELIVERABLE_SUFFIXES


def is_image_deliverable(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_DELIVERABLE_SUFFIXES


def image_data_uri(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError:
        return ""
    guessed, _encoding = mimetypes.guess_type(str(path))
    mime = guessed if guessed and guessed.startswith("image/") else "application/octet-stream"
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def render_corner_header(state: dict[str, Any], *, live: bool) -> str:
    run_name = html_escape(str(state.get("run_name", "ringer")))
    identity = html_escape(str(state.get("identity", "unknown")))
    elapsed = html_escape(fmt_compact_duration(state.get("elapsed_s")))
    dot_class = "live-dot is-live" if live else f"live-dot {final_dot_bucket(state)}"
    clock_label = f"{elapsed} elapsed" if live else f"{elapsed} total"
    return f"""<header class="corner">
    <span class="{dot_class}" aria-hidden="true"></span>
    <span class="eyebrow">Ringer &nbsp;·&nbsp; <b>{run_name}</b> &nbsp;·&nbsp; {identity}</span>
    <span class="clock mono">{clock_label}</span>
  </header>"""


def final_dot_bucket(state: dict[str, Any]) -> str:
    counts = task_status_counts(state)
    if counts["fail"]:
        return "fail"
    if counts["pass"]:
        return "pass"
    return "waiting"


def render_status_html(
    state: dict[str, Any],
    renderer: ArtifactRenderer | None = None,
    *,
    force_wrappers: bool = False,
    page_path: Path | None = None,
) -> str:
    """Tier 0 zero-LLM live status artifact. Rendered on every state flush (~1s)."""
    run_name = html_escape(str(state.get("run_name", "ringer")))
    tasks = state_tasks(state)
    counts = task_status_counts(state)
    briefing = live_briefing_html(state)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{CSP_META_TAG}
<title>ringer &middot; {run_name}</title>
<meta http-equiv="refresh" content="2">
<style>{ARTIFACT_BASE_CSS}</style>
</head>
<body>
<div class="page">
  {render_corner_header(state, live=True)}
  <h1 id="right-now-heading" class="briefing">{briefing}</h1>
  {render_progress_bar(tasks, counts)}
  {render_work_section(state, renderer=renderer, page_path=page_path, force_wrappers=force_wrappers, finished_only=True)}
  <footer>
    <span class="mono">Updated {html_escape(local_time_label())}</span>
    <span>·</span>
    <span>This page updates itself while the work runs.</span>
  </footer>
</div>
</body>
</html>
"""


def render_final_report_html(
    state: dict[str, Any],
    renderer: ArtifactRenderer | None = None,
    *,
    force_wrappers: bool = True,
    page_path: Path | None = None,
) -> str:
    """Feature 4: self-contained final report, rendered once when a run finishes."""
    run_name = html_escape(str(state.get("run_name", "ringer")))
    tasks = state_tasks(state)
    counts = task_status_counts(state)
    briefing = final_briefing_html(state)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{CSP_META_TAG}
<title>ringer report &middot; {run_name}</title>
<style>{ARTIFACT_BASE_CSS}</style>
</head>
<body>
<div class="page">
  {render_corner_header(state, live=False)}
  <h1 id="what-happened-heading" class="briefing">What happened — {briefing}</h1>
  {render_progress_bar(tasks, counts)}
  {render_work_section(state, renderer=renderer, page_path=page_path, force_wrappers=force_wrappers, primary=True)}
  <footer>
    <span class="mono">Finished {html_escape(local_time_label())}</span>
  </footer>
</div>
</body>
</html>
"""


def task_activity_line(task: dict[str, Any], bucket: str) -> str:
    if bucket not in {"working", "retry"}:
        return ""
    activity = task.get("activity") or task.get("last_action") or task.get("last-action") or ""
    return str(activity).strip()


def render_task_links(
    task: dict[str, Any],
    *,
    state: dict[str, Any],
    renderer: ArtifactRenderer | None = None,
    force_wrappers: bool = False,
    page_path: Path | None = None,
) -> str:

    def portable(href_path: Path) -> str:
        # A page viewed over http cannot follow file:// links — resolve
        # anything inside the artifact store to a relative href instead.
        if renderer is not None:
            with contextlib.suppress(Exception):
                resolved = href_path.resolve()
                if resolved.is_relative_to(renderer.artifact_dir.resolve()):
                    return artifact_relative_href(
                        resolved, page_path=page_path, artifact_root=renderer.artifact_dir
                    )
        return file_href(href_path)
    links: list[str] = []
    taskdir_path: Path | None = None
    taskdir = task.get("taskdir")
    if taskdir:
        taskdir_path = Path(str(taskdir))

    task_key = str(task.get("key", "task"))

    report_paths = task.get("report_paths") or {}
    if not isinstance(report_paths, dict):
        report_paths = {}
    for report_name in TASK_REPORT_FILENAMES:
        report_value = report_paths.get(report_name)
        report_file = Path(str(report_value)) if report_value else None
        if report_file is None and taskdir_path is not None:
            report_file = taskdir_path / report_name
        if report_file is not None and report_file.exists():
            if renderer:
                renderer.link_for_source(report_file, state=state, task_key=task_key, force=force_wrappers)
                href = portable(renderer.wrapper_path(run_id=str(state.get("run_id") or "run"), task_key=task_key, source_name=report_file.name)) if not is_html_artifact(report_file) else portable(report_file)
            else:
                href = file_href(report_file)
            links.append(f'<a href="{html_escape(href)}">Read what it found</a>')
            break

    log_path = task.get("log_path")
    worker_log = Path(str(log_path)) if log_path else None
    if worker_log is None and taskdir_path is not None:
        worker_log = taskdir_path / "worker.log"
    if worker_log is not None and worker_log.exists():
        if renderer:
            renderer.link_for_source(worker_log, state=state, task_key=task_key, force=force_wrappers)
            href = portable(renderer.wrapper_path(run_id=str(state.get("run_id") or "run"), task_key=task_key, source_name=worker_log.name))
        else:
            href = file_href(worker_log)
        links.append(f'<a href="{html_escape(href)}">view the work log</a>')

    return " &middot; ".join(links) if links else '<span class="muted">—</span>'


def render_artifact_index_html(
    entries: list[dict[str, Any]],
    renderer: ArtifactRenderer | None = None,
    *,
    force_wrappers: bool = False,
) -> str:
    """Multi-run index: one pane of glass across every run under this state_dir."""
    rows = []
    for entry in entries:
        state_label = str(entry.get("state", "live"))
        fail_n = entry.get("fail", 0) or 0
        color = status_color(state_label if state_label in STATUS_COLORS else ("fail" if fail_n else "pass"))
        run_name = html_escape(str(entry.get("run_name", "ringer")))
        identity = html_escape(str(entry.get("identity", "unknown")))
        elapsed = fmt_duration(entry.get("elapsed_s"))
        pass_n = entry.get("pass", 0)
        links: list[str] = []
        artifact_path = entry.get("artifact_path")
        if artifact_path:
            links.append(f'<a href="{html_escape(file_href(Path(str(artifact_path))))}">live</a>')
        if entry.get("report_ready") and entry.get("report_path"):
            report_path = Path(str(entry["report_path"]))
            href = (
                renderer.link_for_source(
                    report_path,
                    run_id=str(entry.get("run_id") or "run"),
                    run_name=str(entry.get("run_name") or "ringer"),
                    task_key="run",
                    force=force_wrappers,
                )
                if renderer
                else file_href(report_path)
            )
            links.append(f'<a href="{html_escape(href)}">report</a>')
        links_html = " &middot; ".join(links) if links else '<span class="muted">—</span>'
        rows.append(
            f"""<tr>
          <td><span class="chip" style="background:{color}">{html_escape(state_label)}</span></td>
          <td class="mono">{run_name}</td>
          <td class="mono">{identity}</td>
          <td class="mono">{pass_n} pass / {fail_n} fail</td>
          <td class="mono">{elapsed}</td>
          <td class="mono">{links_html}</td>
        </tr>"""
        )
    body = "".join(rows) if rows else '<tr><td colspan="6" class="muted">no runs recorded yet</td></tr>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{CSP_META_TAG}
<title>ringer &middot; all runs</title>
<meta http-equiv="refresh" content="5">
<style>{ARTIFACT_BASE_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>ringer &mdash; all runs</h1>
  <p class="meta">One pane of glass across every run with state under this state_dir.</p>
  <table>
    <thead><tr><th>State</th><th>Run</th><th>Identity</th><th>Result</th><th>Elapsed</th><th>Artifacts</th></tr></thead>
    <tbody>{body}</tbody>
  </table>
</div>
</body>
</html>
"""


def artifact_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return "text/html; charset=utf-8"
    if suffix == ".json":
        return "application/json; charset=utf-8"
    guessed, _encoding = mimetypes.guess_type(str(path))
    if guessed:
        if guessed.startswith("text/"):
            return f"{guessed}; charset=utf-8"
        return guessed
    return "application/octet-stream"


def inject_models_tab_into_ringside_html(html: str) -> str:
    if 'id="models-panel"' in html or 'id="artifacts-panel"' not in html:
        return html
    tabs = """
    <nav class="tabs" id="ringside-tabs" aria-label="Ringside views">
      <button type="button" class="tab" id="runs-tab" aria-selected="true">Runs</button>
      <button type="button" class="tab" id="models-tab" aria-selected="false">Models</button>
    </nav>
"""
    panel = """
      <section id="models-panel" class="panel models-panel" hidden>
        <div id="models-status" class="models-status mono">models not loaded</div>
        <div id="models-table-wrap" class="models-table-wrap">
          <div class="empty">No model results yet. Run './ringer.py models' for the local scoreboard docs.</div>
        </div>
      </section>
"""
    style = """
    .models-panel {
      min-height: calc(100vh - 83px);
      padding: 0 clamp(12px, 2vw, 22px) clamp(20px, 3vw, 30px);
    }
    .models-status {
      padding: 10px 0;
      color: var(--muted);
      font-size: 12px;
      border-bottom: 1px solid var(--hairline);
    }
    .models-status.error { color: var(--fail); }
    .models-table-wrap { overflow: auto; }
    .models-table {
      width: 100%;
      min-width: 860px;
      border-collapse: collapse;
      font-size: 13px;
    }
    .models-table th,
    .models-table td {
      padding: 11px 10px;
      border-bottom: 1px solid var(--hairline);
      vertical-align: middle;
      text-align: left;
    }
    .models-table th {
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    .models-table .numeric { text-align: right; }
    .model-row { cursor: pointer; }
    .model-row:hover,
    .model-row.expanded { background: var(--surface); }
    .model-name-cell { display: grid; gap: 1px; min-width: 220px; }
    .model-display { color: var(--ink); font-weight: 700; }
    .model-slug,
    .models-meta {
      color: var(--muted);
      font-size: 12px;
    }
    .tier-badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 2px 7px;
      border: 1px solid var(--hairline);
      border-radius: 5px;
      color: var(--ink);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .tier-badge.proven {
      border-color: color-mix(in srgb, var(--pass) 48%, var(--hairline));
      color: var(--pass);
    }
    .tier-badge.probation {
      border-color: color-mix(in srgb, var(--accent) 48%, var(--hairline));
      color: var(--accent);
    }
    .model-breakdown td {
      padding: 0;
      background: color-mix(in srgb, var(--surface) 72%, transparent);
    }
    .breakdown-grid {
      display: grid;
      grid-template-columns: minmax(120px, 1fr) repeat(5, minmax(70px, auto));
      gap: 0;
      padding: 8px 10px 10px 46px;
      color: var(--muted);
      font-size: 12px;
    }
    .breakdown-grid > div {
      padding: 5px 8px;
      border-bottom: 1px solid var(--hairline);
      min-width: 0;
    }
    .breakdown-head {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: .06em;
      text-transform: uppercase;
    }
    @media (max-width: 760px) {
      .breakdown-grid {
        grid-template-columns: minmax(110px, 1fr) repeat(2, minmax(64px, auto));
        padding-left: 10px;
      }
      .breakdown-grid .optional { display: none; }
    }
"""
    script = r"""
    function installModelsView() {
      const MODELS_REFRESH_MS = 30000;
      const VIEW_KEY = "ringside-view";
      const runsPanel = document.getElementById("artifacts-panel");
      const modelsPanel = document.getElementById("models-panel");
      const runsTab = document.getElementById("runs-tab");
      const modelsTab = document.getElementById("models-tab");
      const status = document.getElementById("models-status");
      const wrap = document.getElementById("models-table-wrap");
      if (!runsPanel || !modelsPanel || !runsTab || !modelsTab || !status || !wrap) return;
      let payload = null;
      let expandedModel = null;
      let lastFetch = 0;
      let inFlight = false;
      let activeView = "runs";

      function html(value) {
        return String(value ?? "")
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      }

      function numberOrZeroLocal(value) {
        const number = Number(value);
        return Number.isFinite(number) ? number : 0;
      }

      function percent(value) {
        const number = Number(value);
        return Number.isFinite(number) ? `${Math.round(number * 100)}%` : "0%";
      }

      function modelDate(value) {
        const text = String(value || "").trim();
        if (!text) return "unknown";
        const match = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
        if (match) {
          const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
          return date.toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric"});
        }
        const stamp = Date.parse(text);
        return Number.isFinite(stamp)
          ? new Date(stamp).toLocaleDateString("en-US", {month: "long", day: "numeric", year: "numeric"})
          : text;
      }

      function safeClass(value) {
        return String(value || "unknown").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "") || "unknown";
      }

      function groupsFor(bucketId) {
        const groups = Array.isArray(payload?.groups) ? payload.groups : [];
        return groups.filter(group => String(group?.bucket_id || "") === bucketId);
      }

      function breakdown(bucketId) {
        const groups = groupsFor(bucketId);
        if (!groups.length) return '<div class="empty">No per-task breakdown recorded for this model.</div>';
        const cells = [
          '<div class="breakdown-head">Task type</div>',
          '<div class="breakdown-head">Tasks</div>',
          '<div class="breakdown-head">First</div>',
          '<div class="breakdown-head optional">Pass</div>',
          '<div class="breakdown-head optional">Attempts</div>',
          '<div class="breakdown-head optional">Last used</div>',
        ];
        groups.forEach(group => {
          cells.push(
            `<div>${html(group.task_type || "(untyped)")}</div>`,
            `<div>${numberOrZeroLocal(group.tasks).toLocaleString()}</div>`,
            `<div>${html(percent(group.first_try_pass_rate))}</div>`,
            `<div class="optional">${html(percent(group.pass_rate))}</div>`,
            `<div class="optional">${numberOrZeroLocal(group.attempts).toLocaleString()}</div>`,
            `<div class="optional">${html(modelDate(group.last_seen))}</div>`,
          );
        });
        return `<div class="breakdown-grid mono">${cells.join("")}</div>`;
      }

      function renderModels() {
        const rows = Array.isArray(payload?.rollup) ? payload.rollup : [];
        const error = String(payload?.error || "").trim();
        status.classList.toggle("error", Boolean(error));
        status.textContent = error ? `models unavailable: ${error}` : `updated ${modelDate(payload?.generated_at)}`;
        if (!rows.length) {
          wrap.innerHTML = '<div class="empty">No model results yet. Run \'./ringer.py models\' for the local scoreboard docs.</div>';
          return;
        }
        const body = [];
        let rank = 0;
        rows.forEach((row, index) => {
          const bucketId = String(row.bucket_id || `${row.engine || ""}|${row.model || ""}`);
          const expanded = expandedModel === bucketId;
          const tierClass = safeClass(row.tier);
          if (!row.unattributed) rank += 1;
          body.push(
            `<tr class="model-row${expanded ? " expanded" : ""}" data-model="${html(bucketId)}" tabindex="0">`,
            `<td class="numeric">${row.unattributed ? "—" : rank}</td>`,
            '<td><span class="model-name-cell">',
            `<span class="model-display">${html(row.model_display || row.model || "unknown")}</span>`,
            `<span class="model-slug mono">${html(row.unattributed ? `engine: ${row.engine || "unknown"}` : (row.model || "unknown"))}</span>`,
            '</span></td>',
            `<td>${html(row.lab || "(unknown)")}</td>`,
            `<td>${html(row.harness || "unknown")}</td>`,
            `<td>${html(row.access || "unknown")}</td>`,
            `<td><span class="tier-badge ${html(tierClass)}">${html(row.tier || "unknown")}</span></td>`,
            `<td class="numeric">${numberOrZeroLocal(row.tasks).toLocaleString()}</td>`,
            `<td class="numeric">${html(percent(row.first_try_pass_rate))}</td>`,
            `<td class="numeric">${html(percent(row.pass_rate))}</td>`,
            `<td>${html(modelDate(row.last_seen))}</td>`,
            '</tr>',
          );
          if (expanded) body.push(`<tr class="model-breakdown"><td colspan="10">${breakdown(bucketId)}</td></tr>`);
        });
        wrap.innerHTML = [
          '<table class="models-table">',
          '<thead><tr>',
          '<th class="numeric">Rank</th><th>Model</th><th>Lab</th><th>Harness</th><th>API/Plan</th><th>Tier</th>',
          '<th class="numeric">Tasks</th><th class="numeric">First-try %</th><th class="numeric">Pass %</th><th>Last used</th>',
          '</tr></thead>',
          `<tbody>${body.join("")}</tbody>`,
          '</table>',
        ].join("");
      }

      async function fetchModels(force) {
        const now = Date.now();
        if (inFlight || (!force && lastFetch && now - lastFetch < MODELS_REFRESH_MS)) return;
        inFlight = true;
        status.textContent = payload ? "refreshing models..." : "loading models...";
        try {
          const response = await fetch(`/api/models?t=${Date.now()}`, {cache: "no-store"});
          payload = await response.json();
          lastFetch = Date.now();
        } catch (error) {
          payload = {generated_at: new Date().toISOString(), groups: [], rollup: [], error: error?.message || "models unavailable"};
        } finally {
          inFlight = false;
          renderModels();
        }
      }

      function selectView(view, persist = true) {
        activeView = view === "models" ? "models" : "runs";
        runsPanel.hidden = activeView === "models";
        modelsPanel.hidden = activeView !== "models";
        runsTab.setAttribute("aria-selected", String(activeView === "runs"));
        modelsTab.setAttribute("aria-selected", String(activeView === "models"));
        if (persist) localStorage.setItem(VIEW_KEY, activeView);
        if (activeView === "models") fetchModels(true);
      }

      runsTab.addEventListener("click", () => selectView("runs"));
      modelsTab.addEventListener("click", () => selectView("models"));
      wrap.addEventListener("click", event => {
        const row = event.target.closest(".model-row");
        if (!row) return;
        const model = row.getAttribute("data-model") || "";
        expandedModel = expandedModel === model ? null : model;
        renderModels();
      });
      wrap.addEventListener("keydown", event => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const row = event.target.closest(".model-row");
        if (!row) return;
        event.preventDefault();
        const model = row.getAttribute("data-model") || "";
        expandedModel = expandedModel === model ? null : model;
        renderModels();
      });
      setInterval(() => {
        if (activeView === "models") fetchModels(false);
      }, MODELS_REFRESH_MS);
      selectView(localStorage.getItem(VIEW_KEY) === "models" ? "models" : "runs", false);
    }

"""
    html = html.replace("    main {\n", style + "    main {\n", 1)
    html = html.replace("    <main>\n", tabs + "\n    <main>\n", 1)
    html = html.replace("    </main>\n", panel + "    </main>\n", 1)
    html = html.replace("    tickClock();\n", script + "    installModelsView();\n    tickClock();\n", 1)
    return html


def read_ringside_html() -> str:
    try:
        return inject_models_tab_into_ringside_html(RINGSIDE_HTML_PATH.read_text(encoding="utf-8"))
    except OSError:
        return """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>Ringside</title></head>
<body><main id="app">dashboard/ringside.html is missing</main></body>
</html>
"""


def send_response_body(
    handler: BaseHTTPRequestHandler,
    status: HTTPStatus,
    body: bytes,
    *,
    content_type: str,
    no_store: bool = False,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    if no_store:
        handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def send_json_response(handler: BaseHTTPRequestHandler, data: dict[str, Any]) -> None:
    body = json.dumps(data, sort_keys=True).encode("utf-8")
    send_response_body(
        handler,
        HTTPStatus.OK,
        body,
        content_type="application/json; charset=utf-8",
        no_store=True,
    )


def read_json_object(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return default
    return data if isinstance(data, dict) else default


# Derive liveness on read: persisting from an HTTP GET could race a live run's flush.
# It would also bump mtime and move a ghost to the top of the mtime-ordered feed.
def reconcile_dead_run_state(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("state") != "live":
        return data
    pid = data.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return data
    if pid_is_alive(pid):
        return data
    reconciled = dict(data)
    reconciled["state"] = "died"
    return reconciled


def scan_hud_run_states(state_dir: Path, *, limit: int = 12) -> list[dict[str, Any]]:
    runs_dir = state_dir / "runs"
    try:
        paths = [path for path in runs_dir.glob("*.json") if path.is_file()]
    except OSError:
        return []

    def path_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    paths.sort(key=path_mtime, reverse=True)
    runs: list[dict[str, Any]] = []
    for path in paths[:limit]:
        data = read_json_object(path, {})
        if data:
            runs.append(reconcile_dead_run_state(data))
    return runs


def read_active_runs_file() -> dict[str, Any]:
    return read_json_object(active_runs_path(), {})


def resolve_artifact_http_path(artifact_root: Path, request_path: str) -> Path | None:
    if request_path == "/artifacts/library.json":
        relative = "library.json"
    elif request_path.startswith("/artifacts/"):
        relative = request_path[len("/artifacts/") :]
    else:
        return None
    if not relative:
        return None
    decoded = urllib.parse.unquote(relative)
    root = artifact_root.resolve()
    candidate = (root / decoded).resolve()
    if candidate == root or root not in candidate.parents:
        return None
    return candidate


def task_log_path_from_state(state_path: Path, task_key: str) -> Path | None:
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tasks = data.get("tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict) or task.get("key") != task_key:
            continue
        log_path = task.get("log_path")
        if isinstance(log_path, str) and log_path:
            return Path(log_path)
    return None


def run_state_path_for_id(state_dir: Path, run_id: str) -> Path | None:
    if not run_id:
        return None
    runs_root = (state_dir / "runs").resolve()
    candidate = (runs_root / f"{run_id}.json").resolve()
    if candidate.parent != runs_root:
        return None
    return candidate


def hud_task_log_path(state_dir: Path, run_id: str, task_key: str) -> Path | None:
    state_path = run_state_path_for_id(state_dir, run_id)
    if state_path is None:
        return None
    state = read_json_object(state_path, {})
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict) or task.get("key") != task_key:
            continue
        log_path = task.get("log_path")
        if isinstance(log_path, str) and log_path:
            return Path(log_path).expanduser()
        taskdir = task.get("taskdir")
        if isinstance(taskdir, str) and taskdir:
            return Path(taskdir).expanduser() / "worker.log"
    return None


def serve_artifact_path(handler: BaseHTTPRequestHandler, artifact_root: Path, path: str) -> bool:
    artifact_path = resolve_artifact_http_path(artifact_root, path)
    if artifact_path is None:
        return False
    try:
        if not artifact_path.is_file():
            raise FileNotFoundError
        body = artifact_path.read_bytes()
    except (FileNotFoundError, OSError):
        handler.send_error(HTTPStatus.NOT_FOUND)
        return True
    send_response_body(
        handler,
        HTTPStatus.OK,
        body,
        content_type=artifact_content_type(artifact_path),
        no_store=True,
    )
    return True


class PersistentHudServer:
    def __init__(
        self,
        state_dir: Path,
        preferred_port: int = DEFAULT_HUD_PORT,
        *,
        open_viewer: bool = True,
    ) -> None:
        self.state_dir = state_dir
        self.preferred_port = preferred_port
        self.open_viewer = open_viewer
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None
        self.model_log_path: Path | None = None
        self.default_model_log_path: Path = state_dir / "runs.jsonl"
        self.model_db_path: Path | None = None

    def start(self) -> int:
        state_dir = self.state_dir
        artifact_root = artifacts_dir(state_dir)
        preferred_port = self.preferred_port
        server_ref = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = urllib.parse.urlparse(self.path).path
                if path == "/":
                    body = read_ringside_html().encode("utf-8")
                    send_response_body(
                        self,
                        HTTPStatus.OK,
                        body,
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path == "/api/runs":
                    send_json_response(
                        self,
                        {
                            "runs": scan_hud_run_states(state_dir),
                            "active": read_active_runs_file(),
                        },
                    )
                    return
                if path == "/api/models":
                    try:
                        payload = build_models_api_payload(
                            log_path=server_ref.model_log_path or (state_dir / "runs.jsonl"),
                            default_log_path=server_ref.default_model_log_path,
                            db_path=server_ref.model_db_path,
                        )
                    except Exception as exc:
                        payload = {
                            "generated_at": utc_now_iso(),
                            "groups": [],
                            "rollup": [],
                            "error": str(exc) or exc.__class__.__name__,
                        }
                    send_json_response(self, payload)
                    return
                if path.startswith("/api/open-folder"):
                    query = urllib.parse.urlparse(path).query
                    params = urllib.parse.parse_qs(query)
                    name = (params.get("artifact") or [""])[0]
                    run_id = (params.get("run") or [""])[0]
                    artifact_root_dir = (state_dir / "artifacts").resolve()
                    target = artifact_root_dir / "deliverables"
                    if run_id:
                        target = target / sanitize_artifact_name(run_id)
                    if not target.exists():
                        target = artifact_root_dir
                    try:
                        resolved = target.resolve()
                        if resolved != artifact_root_dir and artifact_root_dir not in resolved.parents:
                            self.send_error(HTTPStatus.NOT_FOUND)
                            return
                        if sys.platform == "darwin":
                            subprocess.Popen(["open", str(resolved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            self.send_response(HTTPStatus.NO_CONTENT)
                            self.end_headers()
                        else:
                            self.send_error(HTTPStatus.NOT_IMPLEMENTED)
                    except Exception:
                        self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                if path == "/api/library":
                    # A run that died without cleanup must not sit "live"
                    # forever in the rail — reconcile against real pids on read.
                    with contextlib.suppress(Exception):
                        reconcile_artifact_library_dead_runs(state_dir)
                    send_json_response(
                        self,
                        read_json_object(artifact_library_path(state_dir), {"artifacts": {}}),
                    )
                    return
                if path.startswith("/artifacts/"):
                    if not serve_artifact_path(self, artifact_root, path):
                        self.send_error(HTTPStatus.NOT_FOUND)
                    return
                if path.startswith("/logs/"):
                    relative = path[len("/logs/") :]
                    if "/" not in relative:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    run_id_raw, task_key_raw = relative.split("/", 1)
                    run_id = urllib.parse.unquote(run_id_raw)
                    task_key = urllib.parse.unquote(task_key_raw)
                    log_path = hud_task_log_path(state_dir, run_id, task_key)
                    if log_path is None or not log_path.is_file():
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    body = tail_file_text(log_path, max_bytes=WORKER_LOG_TAIL_BYTES).encode("utf-8")
                    send_response_body(
                        self,
                        HTTPStatus.OK,
                        body,
                        content_type="text/plain; charset=utf-8",
                        no_store=True,
                    )
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        try:
            self.httpd = ThreadingHTTPServer(("127.0.0.1", preferred_port), Handler)
        except OSError as exc:
            raise RuntimeError(
                f"could not start Ringside on 127.0.0.1:{preferred_port}; "
                "that port is already in use. Use --port to choose another port."
            ) from exc
        self.port = int(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="ringer-hud", daemon=True)
        self.thread.start()
        url = f"http://127.0.0.1:{self.port}"
        if self.open_viewer:
            with contextlib.suppress(Exception):
                webbrowser.open(url)
        print(f"Ringside: {url}", flush=True)
        return self.port

    def start_background(self) -> int:
        return self.start()

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


class Dashboard:
    def __init__(
        self,
        state_path: Path,
        preferred_port: int,
        hud_app_path: Path | None = None,
        force_browser: bool = False,
        open_viewer: bool = True,
    ) -> None:
        self.state_path = state_path
        self.preferred_port = preferred_port
        self.hud_app_path = hud_app_path
        self.force_browser = force_browser
        self.open_viewer = open_viewer
        self.httpd: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.port: int | None = None

    def start(self) -> int:
        state_path = self.state_path
        artifact_root = state_path.parent.parent / "artifacts"

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                path = urllib.parse.urlparse(self.path).path
                if path == "/":
                    body = read_dashboard_html().encode("utf-8")
                    send_response_body(
                        self,
                        HTTPStatus.OK,
                        body,
                        content_type="text/html; charset=utf-8",
                    )
                    return
                if path == "/state.json":
                    try:
                        body = state_path.read_bytes()
                    except FileNotFoundError:
                        body = b'{"run_name":"ringer","identity":"unknown","started_at":"","port":null,"dashboard_port":null,"tasks":[],"totals":{"running":0,"done":0,"pass":0,"fail":0,"tokens":0}}'
                    send_response_body(
                        self,
                        HTTPStatus.OK,
                        body,
                        content_type="application/json; charset=utf-8",
                        no_store=True,
                    )
                    return
                if path.startswith("/logs/"):
                    task_key = urllib.parse.unquote(path[len("/logs/") :])
                    log_path = task_log_path_from_state(state_path, task_key)
                    if log_path is None:
                        self.send_error(HTTPStatus.NOT_FOUND)
                        return
                    body = tail_file_text(log_path, max_bytes=WORKER_LOG_TAIL_BYTES).encode("utf-8")
                    send_response_body(
                        self,
                        HTTPStatus.OK,
                        body,
                        content_type="text/plain; charset=utf-8",
                        no_store=True,
                    )
                    return
                if serve_artifact_path(self, artifact_root, path):
                    return
                self.send_error(HTTPStatus.NOT_FOUND)

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        last_error: OSError | None = None
        for port in range(self.preferred_port, self.preferred_port + 50):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            except OSError as exc:
                last_error = exc
                continue
            self.port = int(self.httpd.server_address[1])
            break
        if self.httpd is None or self.port is None:
            raise RuntimeError(f"could not start dashboard: {last_error}")
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="ringer-dashboard", daemon=True)
        self.thread.start()
        url = f"http://localhost:{self.port}"
        # Browser-first: the persistent hud (ensure_hud_running, called from the
        # run path) is what the human watches. Only --browser opens this
        # per-run page directly; the parked Tauri app is never auto-launched.
        if self.open_viewer and self.force_browser:
            open_in_browser(url)
        # The persistent hud (:8700) is the one watch surface; this per-run
        # server is an internal state/log feed. Only advertise it when the
        # user explicitly chose the per-run page with --browser.
        if self.force_browser:
            print(f"Dashboard: {url}", flush=True)
        return self.port

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)


class EvalLogger:
    def __init__(self, config: EvalConfig) -> None:
        self.config = config
        self._conn: Any | None = None
        self._fallback_path = config.jsonl_path
        self._fallback_reason: str | None = None
        if config.backend == "postgres":
            self._connect()

    def log_attempt(self, row: dict[str, Any]) -> None:
        if self._conn is not None:
            db_row = {
                key: value
                for key, value in row.items()
                if key not in {"model", "reasoning_effort", "task_type", "retry"}
            }
            try:
                self._conn.execute(
                    """
                    INSERT INTO swarm_runs (
                        run_id, pattern, task_key, spec, worker_engine, shepherd_model,
                        verify_method, verdict, duration_ms, worker_tokens, notes, orchestrator
                    )
                    VALUES (
                        %(run_id)s, %(pattern)s, %(task_key)s, %(spec)s, %(worker_engine)s,
                        %(shepherd_model)s, %(verify_method)s, %(verdict)s, %(duration_ms)s,
                        %(worker_tokens)s, %(notes)s, %(orchestrator)s
                    )
                    """,
                    db_row,
                )
                return
            except Exception as exc:
                self._fallback_reason = f"Supabase insert failed: {exc}"
                self._close_conn()
        self._write_jsonl(row)

    def close(self) -> None:
        self._close_conn()

    def _connect(self) -> None:
        try:
            import psycopg  # type: ignore[import-not-found]
        except Exception as exc:
            self._fallback_reason = f"psycopg import failed: {exc}"
            return
        if self.config.postgres is None:
            self._fallback_reason = "postgres eval config missing"
            return
        creds = parse_env_file(self.config.postgres.env_file)
        required = [
            "SUPABASE_DB_HOST",
            "SUPABASE_DB_PORT",
            "SUPABASE_DB_USER",
            "SUPABASE_DB_PASSWORD",
            "SUPABASE_DB_NAME",
        ]
        missing = [key for key in required if not creds.get(key)]
        if missing:
            self._fallback_reason = f"missing Supabase env keys: {', '.join(missing)}"
            return
        try:
            self._conn = psycopg.connect(
                host=creds["SUPABASE_DB_HOST"],
                port=int(creds["SUPABASE_DB_PORT"]),
                user=creds["SUPABASE_DB_USER"],
                password=creds["SUPABASE_DB_PASSWORD"],
                dbname=creds["SUPABASE_DB_NAME"],
                autocommit=True,
                connect_timeout=5,
            )
        except Exception as exc:
            self._fallback_reason = f"Supabase connect failed: {exc}"

    def _write_jsonl(self, row: dict[str, Any]) -> None:
        self._fallback_path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(row)
        payload["logged_at"] = datetime.now(timezone.utc).isoformat()
        payload["log_sink"] = "jsonl"
        payload["fallback_reason"] = self._fallback_reason
        with self._fallback_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, sort_keys=True) + "\n")

    def _close_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


def parse_log_date(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 10:
        return ""
    candidate = value[:10]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", candidate):
        return ""
    try:
        datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return ""
    return candidate


def validate_since_date(value: str | None) -> str | None:
    if value is None:
        return None
    if not parse_log_date(value):
        raise ValueError("--since must be YYYY-MM-DD")
    return value


def model_log_row_is_retry(row: dict[str, Any]) -> bool:
    retry = row.get("retry")
    if isinstance(retry, bool):
        return retry
    if isinstance(retry, str) and retry.strip().lower() in {"true", "false"}:
        return retry.strip().lower() == "true"
    notes = row.get("notes", "")
    return isinstance(notes, str) and "retry=true" in notes


def model_log_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def model_log_row_model(row: dict[str, Any]) -> str:
    model = model_log_text(row.get("model"))
    if model:
        return model
    return model_log_text(row.get("worker_engine"))


def model_log_row_is_unattributed(row: dict[str, Any]) -> bool:
    return not model_log_text(row.get("model"))


def model_log_row_reasoning_effort(row: dict[str, Any]) -> str | None:
    effort = model_log_text(row.get("reasoning_effort"))
    return effort or None


def model_log_row_is_reserved_fixture(row: dict[str, Any]) -> bool:
    return model_log_text(row.get("model")) in RESERVED_FIXTURE_MODELS


def model_reasoning_effort_keys(rows: list[dict[str, Any]]) -> set[tuple[str, str, bool]]:
    keys: set[tuple[str, str, bool]] = set()
    for row in rows:
        if model_log_row_is_reserved_fixture(row):
            continue
        if (
            not model_log_row_is_unattributed(row)
            and model_log_row_reasoning_effort(row) is not None
        ):
            keys.add(
                (
                    model_log_row_engine(row),
                    model_log_row_model(row),
                    model_log_row_is_unattributed(row),
                )
            )
    return keys


def model_log_row_task_type(row: dict[str, Any]) -> str:
    task_type = model_log_text(row.get("task_type"))
    return task_type or "(untyped)"


def model_log_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def median_int(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) // 2


def model_log_task_base_key(row: dict[str, Any]) -> tuple[str, str, str, str, str, bool] | None:
    run_id = model_log_text(row.get("run_id"))
    task_key = model_log_text(row.get("task_key"))
    if not run_id or not task_key:
        return None
    unattributed = model_log_row_is_unattributed(row)
    return (
        run_id,
        task_key,
        model_log_row_model(row),
        model_log_row_task_type(row),
        "" if unattributed else (model_log_row_reasoning_effort(row) or ""),
        unattributed,
    )


def group_model_log_tasks(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    grouped: list[list[dict[str, Any]]] = []
    active_by_key: dict[tuple[str, str, str, str, str, bool], int] = {}
    for row in rows:
        key = model_log_task_base_key(row)
        if key is not None and model_log_row_is_retry(row) and key in active_by_key:
            grouped[active_by_key[key]].append(row)
            continue
        grouped.append([row])
        if key is not None:
            active_by_key[key] = len(grouped) - 1
    return grouped


def read_model_log_rows(
    path: Path,
    *,
    since: str | None = None,
    engine: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    try:
        fh = path.open("r", encoding="utf-8")
    except FileNotFoundError:
        return rows, skipped
    with fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if not isinstance(row, dict):
                skipped += 1
                continue
            if engine is not None and model_log_text(row.get("worker_engine")) != engine:
                continue
            rows.append(row)
    if since is not None:
        selected_row_ids: set[int] = set()
        for task_rows in group_model_log_tasks(rows):
            ordered = sorted(
                task_rows,
                key=lambda row: (
                    model_log_text(row.get("logged_at")),
                    1 if model_log_row_is_retry(row) else 0,
                ),
            )
            final_date = parse_log_date(ordered[-1].get("logged_at"))
            if final_date and final_date >= since:
                selected_row_ids.update(id(row) for row in task_rows)
        rows = [row for row in rows if id(row) in selected_row_ids]
    return rows, skipped


def aggregate_model_log_rows(
    rows: list[dict[str, Any]],
    *,
    task_type: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, bool], dict[str, Any]] = {}
    effort_keys = model_reasoning_effort_keys(rows)
    for task_rows in group_model_log_tasks(rows):
        ordered = sorted(
            task_rows,
            key=lambda row: (
                model_log_text(row.get("logged_at")),
                1 if model_log_row_is_retry(row) else 0,
            ),
        )
        first = ordered[0]
        final = ordered[-1]
        if model_log_row_is_reserved_fixture(final):
            continue
        group_engine = model_log_row_engine(final)
        group_model = model_log_row_model(final)
        group_task_type = model_log_row_task_type(final)
        unattributed = model_log_row_is_unattributed(final)
        reasoning_effort = None if unattributed else model_log_row_reasoning_effort(final)
        if model is not None and group_model != model:
            continue
        if task_type is not None and group_task_type != task_type:
            continue
        key = (
            group_engine,
            group_model,
            group_task_type,
            reasoning_effort or "",
            unattributed,
        )
        group = groups.setdefault(
            key,
            {
                "engine": group_engine,
                "model": group_model,
                "task_type": group_task_type,
                "reasoning_effort": reasoning_effort,
                "show_reasoning_effort": (
                    (group_engine, group_model, unattributed) in effort_keys
                ),
                "unattributed": unattributed,
                "tasks": 0,
                "attempts": 0,
                "passed": 0,
                "failed": 0,
                "pass_rate": 0.0,
                "first_try_pass_rate": 0.0,
                "median_duration_ms": None,
                "median_tokens": None,
                "last_seen": "",
                "_first_try_passed": 0,
                "_duration_ms": [],
                "_tokens": [],
            },
        )
        group["tasks"] += 1
        group["attempts"] += len(ordered)
        if model_log_text(final.get("verdict")).upper() == "PASS":
            group["passed"] += 1
        else:
            group["failed"] += 1
        if model_log_text(first.get("verdict")).upper() == "PASS":
            group["_first_try_passed"] += 1
        duration_ms = model_log_int(final.get("duration_ms"))
        if duration_ms is not None:
            group["_duration_ms"].append(duration_ms)
        for row in ordered:
            tokens = model_log_int(row.get("worker_tokens"))
            if tokens is not None:
                group["_tokens"].append(tokens)
        logged_at = model_log_text(final.get("logged_at"))
        if logged_at > group["last_seen"]:
            group["last_seen"] = logged_at

    finalized: list[dict[str, Any]] = []
    for group in groups.values():
        tasks_count = group["tasks"]
        group["pass_rate"] = group["passed"] / tasks_count if tasks_count else 0.0
        group["first_try_pass_rate"] = (
            group["_first_try_passed"] / tasks_count if tasks_count else 0.0
        )
        group["median_duration_ms"] = median_int(group["_duration_ms"])
        group["median_tokens"] = median_int(group["_tokens"])
        finalized.append(
            {
                "engine": group["engine"],
                "model": group["model"],
                "task_type": group["task_type"],
                "reasoning_effort": group["reasoning_effort"],
                "show_reasoning_effort": group["show_reasoning_effort"],
                "unattributed": group["unattributed"],
                "tasks": group["tasks"],
                "attempts": group["attempts"],
                "passed": group["passed"],
                "failed": group["failed"],
                "pass_rate": group["pass_rate"],
                "first_try_pass_rate": group["first_try_pass_rate"],
                "median_duration_ms": group["median_duration_ms"],
                "median_tokens": group["median_tokens"],
                "last_seen": group["last_seen"],
            }
        )
    return sorted(
        finalized,
        key=lambda item: (
            1 if item["unattributed"] else 0,
            item["task_type"],
            -item["pass_rate"],
            -item["first_try_pass_rate"],
            item["engine"],
            item["model"],
            item["reasoning_effort"] or "",
        ),
    )


MODEL_SCOREBOARD_RUN_NAME = "model-scoreboard"
MODEL_SCOREBOARD_IDENTITY = "ringer-models"


def default_model_notes_path() -> Path:
    return Path(__file__).resolve().parent / "docs" / "MODEL-NOTES.md"


def default_model_registry_path() -> Path:
    return Path(__file__).resolve().parent / "registry" / "model-identity.toml"


def default_read_model_db_path() -> Path:
    return ringer_home() / "ringer.db"


def should_use_read_model_db(
    *,
    log_path: Path,
    default_log_path: Path,
    explicit_db: bool,
) -> bool:
    if explicit_db:
        return True
    return log_path.expanduser().resolve() == default_log_path.expanduser().resolve()


@dataclass(frozen=True)
class ModelIdentity:
    model_display: str
    lab: str
    harness: str
    access: str
    alias: bool = False
    confidence: str = ""
    source: str = ""
    last_verified: str = ""
    unregistered: bool = False


@dataclass(frozen=True)
class ModelIdentityRegistry:
    identities: dict[tuple[str, str], ModelIdentity]
    defaults: dict[str, str]
    engine_meta: dict[str, ModelIdentity]

    def resolve(self, engine: str, model_key: str) -> ModelIdentity:
        engine_key = model_log_text(engine)
        raw_model_key = model_log_text(model_key)
        lookup_key = raw_model_key or self.defaults.get(engine_key, "")
        identity = self.identities.get((engine_key, lookup_key))
        if identity is not None:
            return identity
        meta = self.engine_meta.get(engine_key)
        if raw_model_key.startswith("openrouter/"):
            slug = raw_model_key.removeprefix("openrouter/")
            org = slug.split("/", 1)[0] if "/" in slug else ""
            return ModelIdentity(
                model_display=raw_model_key,
                lab=f"{org}?" if org else "(unverified)",
                harness=(meta.harness if meta else "OpenCode"),
                access=(meta.access if meta else "OpenRouter API"),
                confidence="fallback",
                source="unlisted OpenRouter slug",
                unregistered=True,
            )
        if raw_model_key:
            return ModelIdentity(
                model_display=raw_model_key,
                lab="(unverified)",
                harness=meta.harness if meta else (engine_key or "unknown"),
                access=meta.access if meta else "unknown",
                confidence="fallback",
                source="unregistered model slug",
                unregistered=True,
            )
        unknown = engine_key or "unknown"
        return ModelIdentity(
            model_display=unknown,
            lab="(unknown)",
            harness=unknown,
            access="unknown",
            confidence="unknown",
            source="",
        )


EMPTY_MODEL_IDENTITY_REGISTRY = ModelIdentityRegistry({}, {}, {})


def load_model_identity_registry(path: Path | None = None) -> ModelIdentityRegistry:
    registry_path = (path or default_model_registry_path()).expanduser().resolve()
    try:
        with registry_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return EMPTY_MODEL_IDENTITY_REGISTRY
    engines_raw = data.get("engines", {})
    if not isinstance(engines_raw, dict):
        return EMPTY_MODEL_IDENTITY_REGISTRY
    identities: dict[tuple[str, str], ModelIdentity] = {}
    defaults: dict[str, str] = {}
    engine_meta: dict[str, ModelIdentity] = {}
    for engine_name, raw_engine in engines_raw.items():
        if not isinstance(raw_engine, dict):
            continue
        engine = str(engine_name).strip()
        if not engine:
            continue
        harness = model_log_text(raw_engine.get("harness")) or engine
        access = model_log_text(raw_engine.get("access")) or "unknown"
        default_key = model_log_text(raw_engine.get("default_model_key"))
        if default_key:
            defaults[engine] = default_key
        engine_meta[engine] = ModelIdentity(
            model_display=engine,
            lab="(unknown)",
            harness=harness,
            access=access,
            confidence="engine",
            source="",
        )
        models_raw = raw_engine.get("models", {})
        if not isinstance(models_raw, dict):
            continue
        for model_key_raw, raw_model in models_raw.items():
            if not isinstance(raw_model, dict):
                continue
            model_key = str(model_key_raw).strip()
            if not model_key:
                continue
            identities[(engine, model_key)] = ModelIdentity(
                model_display=model_log_text(raw_model.get("display")) or model_key,
                lab=model_log_text(raw_model.get("lab")) or "(unknown)",
                harness=harness,
                access=access,
                alias=bool(raw_model.get("alias", False)),
                confidence=model_log_text(raw_model.get("confidence")),
                source=model_log_text(raw_model.get("source")),
                last_verified=model_log_text(raw_model.get("last_verified")),
            )
    return ModelIdentityRegistry(identities, defaults, engine_meta)


def model_log_row_engine(row: dict[str, Any]) -> str:
    return model_log_text(row.get("worker_engine") if "worker_engine" in row else row.get("engine"))


def row_identity_fields(row: dict[str, Any], registry: ModelIdentityRegistry) -> dict[str, Any]:
    if model_log_row_is_unattributed(row):
        engine = model_log_row_engine(row)
        meta = registry.engine_meta.get(engine)
        return {
            "model_display": UNATTRIBUTED_MODEL_DISPLAY,
            "lab": "(unknown)",
            "harness": meta.harness if meta else (engine or "unknown"),
            "access": meta.access if meta else "unknown",
            "alias": False,
            "last_verified": "",
            "unregistered": False,
        }
    identity = registry.resolve(model_log_row_engine(row), model_log_text(row.get("model")))
    return {
        "model_display": identity.model_display,
        "lab": identity.lab,
        "harness": identity.harness,
        "access": identity.access,
        "alias": identity.alias,
        "last_verified": identity.last_verified,
        "unregistered": identity.unregistered,
    }


def task_final_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    finals: list[dict[str, Any]] = []
    for task_rows in group_model_log_tasks(rows):
        ordered = sorted(
            task_rows,
            key=lambda row: (
                model_log_text(row.get("logged_at")),
                1 if model_log_row_is_retry(row) else 0,
            ),
        )
        if ordered:
            finals.append(ordered[-1])
    return finals


def enrich_model_groups_with_identity(
    groups: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    registry: ModelIdentityRegistry,
    *,
    include_task_type: bool,
    catalog_models: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    catalog_by_id = catalog_models_by_id(catalog_models or [])
    identity_rows: dict[tuple[Any, ...], dict[str, Any]] = {}
    latest: dict[tuple[Any, ...], str] = {}
    for row in task_final_rows(rows):
        if model_log_row_is_reserved_fixture(row):
            continue
        group_engine = model_log_row_engine(row)
        group_model = model_log_row_model(row)
        group_task_type = model_log_row_task_type(row)
        unattributed = model_log_row_is_unattributed(row)
        reasoning_effort = None if unattributed else model_log_row_reasoning_effort(row)
        key: tuple[Any, ...]
        key = (
            (group_engine, group_model, group_task_type, reasoning_effort, unattributed)
            if include_task_type
            else (group_engine, group_model, reasoning_effort, unattributed)
        )
        logged_at = model_log_text(row.get("logged_at"))
        if key not in latest or logged_at >= latest[key]:
            latest[key] = logged_at
            identity = row_identity_fields(row, registry)
            if identity.get("unregistered"):
                identity.update(
                    catalog_identity_fields(model_log_text(row.get("model")), catalog_by_id)
                )
            identity_rows[key] = identity
    enriched: list[dict[str, Any]] = []
    for group in groups:
        key = (
            (
                str(group.get("engine") or ""),
                str(group.get("model") or ""),
                str(group.get("task_type") or ""),
                group.get("reasoning_effort"),
                bool(group.get("unattributed")),
            )
            if include_task_type
            else (
                str(group.get("engine") or ""),
                str(group.get("model") or ""),
                group.get("reasoning_effort"),
                bool(group.get("unattributed")),
            )
        )
        item = dict(group)
        item.update(
            identity_rows.get(
                key,
                {
                    "model_display": str(group.get("model") or ""),
                    "lab": "(unknown)",
                    "harness": "unknown",
                    "access": "unknown",
                    "alias": False,
                    "last_verified": "",
                    "unregistered": bool(group.get("model")),
                },
            )
        )
        if item.get("show_reasoning_effort") and not item.get("unattributed"):
            effort = item.get("reasoning_effort") or "(effort unrecorded)"
            item["model_display"] = f"{item['model_display']} · {effort}"
        if item.get("unattributed"):
            # The aggregation helper retains the engine as a legacy grouping key.
            # Public payloads must not expose harness branding as a model identity.
            item["model"] = ""
        item["bucket_id"] = "|".join(
            (
                str(item.get("engine") or ""),
                str(item.get("model") or ""),
                str(item.get("reasoning_effort") or ""),
                "unattributed" if item.get("unattributed") else "model",
            )
        )
        enriched.append(item)
    return enriched


def ensure_sqlite_available() -> Any:
    if sqlite3 is None:
        raise RuntimeError("sqlite3 is unavailable")
    return sqlite3


def connect_read_model_db(path: Path) -> Any:
    sqlite = ensure_sqlite_available()
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite.connect(str(path))
    conn.row_factory = sqlite.Row
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def connect_read_model_db_readonly(path: Path) -> Any:
    sqlite = ensure_sqlite_available()
    path = path.expanduser().resolve()
    if not path.exists():
        raise RuntimeError(f"read model database missing: {path}")
    uri_path = urllib.parse.quote(path.as_posix(), safe="/")
    conn = sqlite.connect(f"file:{uri_path}?mode=ro", uri=True)
    conn.row_factory = sqlite.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def read_model_table_exists(conn: Any, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (name,),
    ).fetchone()
    return row is not None


def read_model_column_exists(conn: Any, table: str, column: str) -> bool:
    return any(str(row[1]) == column for row in conn.execute(f"PRAGMA table_info({table})"))


def create_read_model_schema(conn: Any) -> None:
    schema_table_exists = read_model_table_exists(conn, "schema_version")
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0] or 0)
    schema_version = None
    if schema_table_exists:
        row = conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if row is not None:
            schema_version = int(row[0])
    needs_stamp = user_version != 3 or schema_version != 3
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY,
            run_id TEXT,
            task_key TEXT,
            logged_at TEXT,
            engine TEXT,
            model TEXT,
            reported_model TEXT,
            expected_model TEXT,
            reasoning_effort TEXT,
            task_type TEXT,
            retry INTEGER,
            verdict TEXT,
            duration_ms INTEGER,
            worker_tokens INTEGER,
            orchestrator TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_attempts_model_task_type
            ON attempts(model, task_type);
        CREATE INDEX IF NOT EXISTS idx_attempts_logged_at
            ON attempts(logged_at);

        CREATE TABLE IF NOT EXISTS catalog_models (
            id TEXT PRIMARY KEY,
            name TEXT,
            context_length INTEGER,
            prompt_per_m REAL,
            completion_per_m REAL,
            free INTEGER,
            variable_pricing INTEGER,
            pricing_unknown INTEGER,
            fetched_at TEXT,
            modality TEXT
        );
        CREATE TABLE IF NOT EXISTS catalog_events (
            id INTEGER PRIMARY KEY,
            ts TEXT,
            kind TEXT,
            model_id TEXT,
            payload TEXT
        );
        CREATE TABLE IF NOT EXISTS identity (
            engine TEXT NOT NULL,
            model_key TEXT NOT NULL,
            model_display TEXT,
            lab TEXT,
            harness TEXT,
            access TEXT,
            alias INTEGER,
            confidence TEXT,
            source TEXT,
            last_verified TEXT,
            PRIMARY KEY (engine, model_key)
        );
        CREATE TABLE IF NOT EXISTS identity_defaults (
            engine TEXT PRIMARY KEY,
            default_model_key TEXT,
            harness TEXT,
            access TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    if not read_model_column_exists(conn, "attempts", "reasoning_effort"):
        conn.execute("ALTER TABLE attempts ADD COLUMN reasoning_effort TEXT")
    if not read_model_column_exists(conn, "attempts", "reported_model"):
        conn.execute("ALTER TABLE attempts ADD COLUMN reported_model TEXT")
    if not read_model_column_exists(conn, "attempts", "expected_model"):
        conn.execute("ALTER TABLE attempts ADD COLUMN expected_model TEXT")
    if not read_model_column_exists(conn, "identity", "lab"):
        conn.execute("ALTER TABLE identity ADD COLUMN lab TEXT")
    if not read_model_column_exists(conn, "identity", "alias"):
        conn.execute("ALTER TABLE identity ADD COLUMN alias INTEGER")
    if not read_model_column_exists(conn, "identity", "last_verified"):
        conn.execute("ALTER TABLE identity ADD COLUMN last_verified TEXT")
    if needs_stamp:
        conn.executescript(
            """
            DELETE FROM schema_version;
            INSERT INTO schema_version(version) VALUES (3);
            PRAGMA user_version = 3;
            """
        )


def drop_read_model_tables(conn: Any) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS attempts;
        DROP TABLE IF EXISTS catalog_models;
        DROP TABLE IF EXISTS catalog_events;
        DROP TABLE IF EXISTS identity;
        DROP TABLE IF EXISTS identity_defaults;
        DROP TABLE IF EXISTS sync_state;
        DROP TABLE IF EXISTS schema_version;
        """
    )


def read_log_rows_from_offset(path: Path, offset: int) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    skipped = 0
    final_offset = offset
    try:
        with path.open("rb") as fh:
            fh.seek(offset)
            while True:
                line_start = fh.tell()
                raw_line = fh.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    final_offset = line_start
                    break
                final_offset = fh.tell()
                try:
                    line = raw_line.decode("utf-8")
                    row = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    skipped += 1
                    continue
                if not isinstance(row, dict):
                    skipped += 1
                    continue
                rows.append(row)
    except FileNotFoundError:
        return [], 0, 0
    return rows, skipped, final_offset


def read_catalog_events_from_offset(path: Path, offset: int) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    final_offset = offset
    try:
        with path.expanduser().open("rb") as fh:
            fh.seek(offset)
            while True:
                line_start = fh.tell()
                raw_line = fh.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    final_offset = line_start
                    break
                final_offset = fh.tell()
                try:
                    event = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except FileNotFoundError:
        return [], 0
    return events, final_offset


def insert_attempt_rows(conn: Any, rows: list[dict[str, Any]]) -> int:
    payloads: list[tuple[Any, ...]] = []
    for row in rows:
        payloads.append(
            (
                model_log_text(row.get("run_id")),
                model_log_text(row.get("task_key")),
                model_log_text(row.get("logged_at")),
                model_log_row_engine(row),
                model_log_text(row.get("model")),
                model_log_text(row.get("reported_model")) or None,
                model_log_text(row.get("expected_model")) or None,
                model_log_row_reasoning_effort(row),
                model_log_text(row.get("task_type")),
                1 if model_log_row_is_retry(row) else 0,
                model_log_text(row.get("verdict")),
                model_log_int(row.get("duration_ms")),
                model_log_int(row.get("worker_tokens")),
                model_log_text(row.get("orchestrator")),
            )
        )
    if payloads:
        conn.executemany(
            """
            INSERT INTO attempts (
                run_id, task_key, logged_at, engine, model, reported_model, expected_model,
                reasoning_effort, task_type, retry,
                verdict, duration_ms, worker_tokens, orchestrator
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payloads,
        )
    return len(payloads)


def read_sync_state_value(conn: Any, key: str) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    return str(row["value"])


def read_sync_state_int(conn: Any, key: str, default: int = 0) -> int:
    value = read_sync_state_value(conn, key)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def write_sync_state_values(conn: Any, values: dict[str, int | str]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO sync_state(key, value) VALUES (?, ?)",
        [(key, str(value)) for key, value in values.items()],
    )


def file_sync_metadata(path: Path) -> tuple[int, int]:
    try:
        stat = path.expanduser().stat()
    except FileNotFoundError:
        return -1, 0
    return int(stat.st_mtime_ns), int(stat.st_size)


def insert_catalog_event_rows(conn: Any, events: list[dict[str, Any]]) -> int:
    event_payloads: list[tuple[Any, ...]] = []
    for event in events:
        event_payloads.append(
            (
                model_log_text(event.get("ts")),
                model_log_text(event.get("kind")),
                model_log_text(event.get("id")),
                json.dumps(event, sort_keys=True),
            )
        )
    if event_payloads:
        conn.executemany(
            "INSERT INTO catalog_events(ts, kind, model_id, payload) VALUES (?, ?, ?, ?)",
            event_payloads,
        )
    return len(event_payloads)


def refresh_catalog_tables(conn: Any, catalog_path: Path) -> None:
    catalog_path = catalog_path.expanduser().resolve()
    changes_path = catalog_changes_path(catalog_path)
    catalog_mtime, catalog_size = file_sync_metadata(catalog_path)
    changes_mtime, changes_size = file_sync_metadata(changes_path)
    catalog_unchanged = (
        read_sync_state_int(conn, "catalog_snapshot_mtime_ns", -2) == catalog_mtime
        and read_sync_state_int(conn, "catalog_snapshot_size", -2) == catalog_size
    )
    changes_unchanged = (
        read_sync_state_int(conn, "catalog_changes_mtime_ns", -2) == changes_mtime
        and read_sync_state_int(conn, "catalog_changes_size", -2) == changes_size
    )
    if catalog_unchanged and changes_unchanged:
        return

    if not catalog_unchanged:
        conn.execute("DELETE FROM catalog_models")
        try:
            catalog_models = load_catalog_snapshot(catalog_path)
        except (OSError, json.JSONDecodeError, ValueError):
            catalog_models = []
        payloads: list[tuple[Any, ...]] = []
        for model in catalog_models:
            normalized = normalize_catalog_for_scoreboard(model)
            model_id = model_log_text(normalized.get("id"))
            if not model_id:
                continue
            payloads.append(
                (
                    model_id,
                    model_log_text(normalized.get("name")),
                    model_log_int(normalized.get("context_length")),
                    normalized.get("prompt_per_m"),
                    normalized.get("completion_per_m"),
                    1 if normalized.get("free") else 0,
                    1 if normalized.get("variable_pricing") else 0,
                    1 if normalized.get("pricing_unknown") else 0,
                    model_log_text(normalized.get("fetched_at")),
                    model_log_text(normalized.get("modality")),
                )
            )
        if payloads:
            conn.executemany(
                """
                INSERT INTO catalog_models (
                    id, name, context_length, prompt_per_m, completion_per_m, free,
                    variable_pricing, pricing_unknown, fetched_at, modality
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                payloads,
            )
        write_sync_state_values(
            conn,
            {
                "catalog_snapshot_mtime_ns": catalog_mtime,
                "catalog_snapshot_size": catalog_size,
            },
        )

    if not changes_unchanged:
        previous_changes_mtime = read_sync_state_value(conn, "catalog_changes_mtime_ns")
        previous_changes_size = read_sync_state_int(conn, "catalog_changes_size", 0)
        previous_offset = read_sync_state_int(conn, "catalog_changes_offset", 0)
        append_only = (
            previous_changes_mtime is not None
            and changes_size >= previous_changes_size
            and previous_offset <= changes_size
        )
        if append_only:
            events, new_offset = read_catalog_events_from_offset(changes_path, previous_offset)
        else:
            conn.execute("DELETE FROM catalog_events")
            events, new_offset = read_catalog_events_from_offset(changes_path, 0)
        insert_catalog_event_rows(conn, events)
        write_sync_state_values(
            conn,
            {
                "catalog_changes_mtime_ns": changes_mtime,
                "catalog_changes_size": changes_size,
                "catalog_changes_offset": new_offset,
            },
        )


def refresh_identity_tables(conn: Any, registry_path: Path) -> None:
    registry = load_model_identity_registry(registry_path)
    conn.execute("DELETE FROM identity")
    conn.execute("DELETE FROM identity_defaults")
    if registry.identities:
        conn.executemany(
            """
            INSERT INTO identity (
                engine, model_key, model_display, lab, harness, access, alias, confidence, source,
                last_verified
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    engine,
                    model_key,
                    identity.model_display,
                    identity.lab,
                    identity.harness,
                    identity.access,
                    1 if identity.alias else 0,
                    identity.confidence,
                    identity.source,
                    identity.last_verified,
                )
                for (engine, model_key), identity in sorted(registry.identities.items())
            ],
        )
    if registry.engine_meta:
        conn.executemany(
            """
            INSERT INTO identity_defaults(engine, default_model_key, harness, access)
            VALUES (?, ?, ?, ?)
            """,
            [
                (
                    engine,
                    registry.defaults.get(engine, ""),
                    identity.harness,
                    identity.access,
                )
                for engine, identity in sorted(registry.engine_meta.items())
            ],
        )


@dataclass(frozen=True)
class ReadModelSyncResult:
    db_path: Path
    log_path: Path
    attempts_inserted: int
    skipped: int
    offset: int
    rebuilt: bool


def rebuild_read_model_db(
    db_path: Path,
    log_path: Path,
    *,
    catalog_path: Path | None = None,
    registry_path: Path | None = None,
) -> ReadModelSyncResult:
    db_path = db_path.expanduser().resolve()
    log_path = log_path.expanduser().resolve()
    catalog_path = (catalog_path or default_catalog_path()).expanduser().resolve()
    registry_path = (registry_path or default_model_registry_path()).expanduser().resolve()
    with contextlib.closing(connect_read_model_db(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            drop_read_model_tables(conn)
            create_read_model_schema(conn)
            rows, skipped, offset = read_log_rows_from_offset(log_path, 0)
            inserted = insert_attempt_rows(conn, rows)
            refresh_catalog_tables(conn, catalog_path)
            refresh_identity_tables(conn, registry_path)
            write_sync_state_values(conn, {"log_offset": offset})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return ReadModelSyncResult(db_path, log_path, inserted, skipped, offset, True)


def sync_read_model_db(
    db_path: Path,
    log_path: Path,
    *,
    catalog_path: Path | None = None,
    registry_path: Path | None = None,
) -> ReadModelSyncResult:
    db_path = db_path.expanduser().resolve()
    log_path = log_path.expanduser().resolve()
    catalog_path = (catalog_path or default_catalog_path()).expanduser().resolve()
    registry_path = (registry_path or default_model_registry_path()).expanduser().resolve()
    try:
        log_size = log_path.stat().st_size
    except FileNotFoundError:
        log_size = 0
    with contextlib.closing(connect_read_model_db(db_path)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            create_read_model_schema(conn)
            offset = read_sync_state_int(conn, "log_offset", 0)
            if log_size < offset:
                conn.rollback()
                return rebuild_read_model_db(
                    db_path,
                    log_path,
                    catalog_path=catalog_path,
                    registry_path=registry_path,
                )
            rows, skipped, new_offset = read_log_rows_from_offset(log_path, offset)
            inserted = insert_attempt_rows(conn, rows)
            refresh_catalog_tables(conn, catalog_path)
            refresh_identity_tables(conn, registry_path)
            write_sync_state_values(conn, {"log_offset": new_offset})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return ReadModelSyncResult(db_path, log_path, inserted, skipped, new_offset, False)


def load_identity_registry_from_db(conn: Any) -> ModelIdentityRegistry:
    identities: dict[tuple[str, str], ModelIdentity] = {}
    defaults: dict[str, str] = {}
    engine_meta: dict[str, ModelIdentity] = {}
    for row in conn.execute("SELECT * FROM identity"):
        engine = model_log_text(row["engine"])
        model_key = model_log_text(row["model_key"])
        if not engine or not model_key:
            continue
        identities[(engine, model_key)] = ModelIdentity(
            model_display=model_log_text(row["model_display"]) or model_key,
            lab=model_log_text(row["lab"]) or "(unknown)",
            harness=model_log_text(row["harness"]) or engine,
            access=model_log_text(row["access"]) or "unknown",
            alias=bool(row["alias"]),
            confidence=model_log_text(row["confidence"]),
            source=model_log_text(row["source"]),
            last_verified=model_log_text(row["last_verified"]),
        )
    for row in conn.execute("SELECT * FROM identity_defaults"):
        engine = model_log_text(row["engine"])
        if not engine:
            continue
        defaults[engine] = model_log_text(row["default_model_key"])
        engine_meta[engine] = ModelIdentity(
            model_display=engine,
            lab="(unknown)",
            harness=model_log_text(row["harness"]) or engine,
            access=model_log_text(row["access"]) or "unknown",
            confidence="engine",
            source="",
        )
    return ModelIdentityRegistry(identities, defaults, engine_meta)


def db_attempt_rows(
    db_path: Path,
    *,
    since: str | None = None,
    engine: str | None = None,
) -> tuple[list[dict[str, Any]], ModelIdentityRegistry]:
    with contextlib.closing(connect_read_model_db_readonly(db_path)) as conn:
        query = """
            SELECT run_id, task_key, logged_at, engine, model, reported_model, expected_model,
                   reasoning_effort, task_type, retry,
                   verdict, duration_ms, worker_tokens, orchestrator
            FROM attempts
        """
        params: list[Any] = []
        if engine is not None:
            query += " WHERE engine = ?"
            params.append(engine)
        query += " ORDER BY id"
        rows = [
            {
                "run_id": row["run_id"],
                "task_key": row["task_key"],
                "logged_at": row["logged_at"],
                "worker_engine": row["engine"],
                "model": row["model"],
                "reported_model": row["reported_model"],
                "expected_model": row["expected_model"],
                "reasoning_effort": row["reasoning_effort"],
                "task_type": row["task_type"],
                "retry": bool(row["retry"]),
                "verdict": row["verdict"],
                "duration_ms": row["duration_ms"],
                "worker_tokens": row["worker_tokens"],
                "orchestrator": row["orchestrator"],
            }
            for row in conn.execute(query, params)
        ]
        registry = load_identity_registry_from_db(conn)
    if since is not None:
        selected_row_ids: set[int] = set()
        for task_rows in group_model_log_tasks(rows):
            ordered = sorted(
                task_rows,
                key=lambda row: (
                    model_log_text(row.get("logged_at")),
                    1 if model_log_row_is_retry(row) else 0,
                ),
            )
            final_date = parse_log_date(ordered[-1].get("logged_at"))
            if final_date and final_date >= since:
                selected_row_ids.update(id(row) for row in task_rows)
        rows = [row for row in rows if id(row) in selected_row_ids]
    return rows, registry


def db_catalog_models(db_path: Path) -> list[dict[str, Any]]:
    with contextlib.closing(connect_read_model_db_readonly(db_path)) as conn:
        rows = [
            {
                "id": row["id"],
                "name": row["name"],
                "context_length": row["context_length"],
                "prompt_per_m": row["prompt_per_m"],
                "completion_per_m": row["completion_per_m"],
                "free": bool(row["free"]),
                "variable_pricing": bool(row["variable_pricing"]),
                "pricing_unknown": bool(row["pricing_unknown"]),
                "fetched_at": row["fetched_at"],
                "modality": row["modality"],
            }
            for row in conn.execute("SELECT * FROM catalog_models ORDER BY id")
        ]
        return rows


def db_catalog_events(db_path: Path, *, limit: int = 20) -> list[dict[str, Any]]:
    with contextlib.closing(connect_read_model_db_readonly(db_path)) as conn:
        rows = conn.execute(
            "SELECT payload FROM catalog_events ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    events: list[dict[str, Any]] = []
    for row in rows:
        try:
            event = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def run_db_command(config: AppConfig, args: argparse.Namespace) -> int:
    db_path = (args.db or default_read_model_db_path()).expanduser().resolve()
    log_path = (args.log or config.eval.jsonl_path).expanduser().resolve()
    catalog_path = (getattr(args, "catalog_file", None) or default_catalog_path()).expanduser().resolve()
    registry_path = (getattr(args, "registry", None) or default_model_registry_path()).expanduser().resolve()
    if args.db_command == "rebuild":
        result = rebuild_read_model_db(
            db_path,
            log_path,
            catalog_path=catalog_path,
            registry_path=registry_path,
        )
    else:
        result = sync_read_model_db(
            db_path,
            log_path,
            catalog_path=catalog_path,
            registry_path=registry_path,
        )
    action = "rebuild" if result.rebuilt else "sync"
    print(
        f"db {action}: {result.db_path} "
        f"attempts={result.attempts_inserted} skipped={result.skipped} offset={result.offset}"
    )
    return 0


def normalize_notes_match_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def parse_model_notes_sections(path: Path) -> dict[str, list[str]]:
    """Return dated bullet blocks keyed by the raw level-2 heading text."""
    try:
        lines = path.expanduser().read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return {}
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None
    current_bullets: list[str] = []
    active_bullet: list[str] | None = None

    def flush_bullet() -> None:
        nonlocal active_bullet
        if active_bullet is not None:
            text = "\n".join(active_bullet).strip()
            if re.search(r"\b\d{4}-\d{2}-\d{2}\b", text):
                current_bullets.append(text)
            active_bullet = None

    def flush_section() -> None:
        flush_bullet()
        if current_heading is not None:
            sections[current_heading] = list(current_bullets)

    for line in lines:
        heading_match = re.match(r"^##\s+(.+?)\s*$", line)
        if heading_match:
            flush_section()
            current_heading = heading_match.group(1).strip()
            current_bullets = []
            active_bullet = None
            continue
        if current_heading is None:
            continue
        if line.startswith("## "):
            flush_section()
            current_heading = None
            current_bullets = []
            active_bullet = None
            continue
        if line.startswith("- "):
            flush_bullet()
            active_bullet = [line[2:].strip()]
            continue
        if active_bullet is not None and (line.startswith("  ") or not line.strip()):
            active_bullet.append(line.strip())
            continue
        flush_bullet()
    flush_section()
    return sections


def model_judgment_notes(model_id: str, notes_sections: dict[str, list[str]]) -> list[str]:
    needle = normalize_notes_match_text(model_id)
    if not needle:
        return []
    id_boundary = r"A-Za-z0-9._/:-"
    needle_re = re.compile(rf"(?<![{id_boundary}]){re.escape(needle)}(?![{id_boundary}])")
    matches: list[tuple[int, int, int, list[str]]] = []
    for index, (heading, bullets) in enumerate(notes_sections.items()):
        normalized_heading = normalize_notes_match_text(heading)
        if not needle_re.search(normalized_heading):
            continue
        exact_score = 1 if normalized_heading == needle else 0
        matches.append((exact_score, len(normalized_heading), -index, bullets))
    if not matches:
        return []
    return max(matches, key=lambda item: (item[0], item[1], item[2]))[3]


def strip_inline_markdown(value: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", value)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def normalized_judgment_note(item: str) -> tuple[str, str] | None:
    text = re.sub(r"\s+", " ", item).strip()
    match = re.match(r"^-?\s*(\d{4}-\d{2}-\d{2})\s+(?:[-\u2013\u2014]+\s*)?(.*)$", text)
    if not match:
        return None
    body = strip_inline_markdown(match.group(2))
    if not body:
        return None
    date = humanized_log_date(match.group(1))
    short_date = re.sub(r",\s*\d{4}$", "", date)
    return short_date, body


def note_date_key(item: str) -> str:
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", item)
    return match.group(0) if match else ""


def render_notes_list(items: list[str], *, notes_path: Path | None = None, limit: int = 5) -> str:
    if not items:
        return '<p class="empty-note">no judgment notes yet</p>'
    ordered_items = sorted(items, key=note_date_key, reverse=True)
    rendered = []
    for item in ordered_items[:limit]:
        note = normalized_judgment_note(item)
        if note is None:
            body = strip_inline_markdown(item)
            if not body:
                continue
            rendered.append(f"<li><span>{html_escape(body)}</span></li>")
            continue
        date, body = note
        rendered.append(
            f'<li><time>{html_escape(date)}</time><span>{html_escape(body)}</span></li>'
        )
    if not rendered:
        return '<p class="empty-note">no judgment notes yet</p>'
    more = ""
    if len(ordered_items) > limit and notes_path is not None:
        more = (
            f'<li class="more-notes">{source_file_link(notes_path, "more in model notes")}</li>'
        )
    return f'<ul class="notes-list">{"".join(rendered)}{more}</ul>'


def normalize_catalog_for_scoreboard(model: dict[str, Any]) -> dict[str, Any]:
    if "prompt_per_m" in model and "completion_per_m" in model:
        return model
    if isinstance(model.get("pricing"), dict):
        return normalize_catalog_model(model, fetched_at=str(model.get("fetched_at") or ""))
    return model


def catalog_models_by_id(catalog_models: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for model in catalog_models:
        model_id = str(model.get("id") or "").strip()
        if model_id:
            by_id[model_id] = normalize_catalog_for_scoreboard(model)
    return by_id


def catalog_identity_fields(
    model_key: str,
    catalog_by_id: dict[str, dict[str, Any]],
) -> dict[str, str]:
    if not model_key.startswith("openrouter/"):
        return {}
    catalog_id = model_key.removeprefix("openrouter/")
    model = catalog_by_id.get(catalog_id) or catalog_by_id.get(model_key)
    if model is None:
        return {}
    name = model_log_text(model.get("name"))
    if not name or name in {catalog_id, model_key}:
        return {}
    display = name.removesuffix(" (free)").strip()
    org = catalog_id.split("/", 1)[0] if "/" in catalog_id else ""
    lab = f"{org}?" if org else "(unverified)"
    if ":" in display:
        prefix, candidate = (part.strip() for part in display.split(":", 1))
        if candidate:
            display = candidate
        if prefix:
            lab = f"{prefix}?"
    return {"model_display": display, "lab": lab}


def model_scoreboard_tier(tasks: int, first_try_pass_rate: float) -> str:
    # Same promotion rule as proven_model_group: volume alone never proves a
    # model — a 0% pass rate with many tasks is evidence against, not for.
    if tasks >= PROVEN_MIN_TASKS and first_try_pass_rate >= PROVEN_MIN_FIRST_TRY:
        return "proven"
    return "probation"


def model_scoreboard_tier_rank(tier: str) -> int:
    return {"proven": 0, "probation": 1}.get(tier, 3)


def aggregate_model_scoreboard_rows(
    rows: list[dict[str, Any]],
    *,
    task_type: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    models: dict[tuple[str, str, str, bool], dict[str, Any]] = {}
    effort_keys = model_reasoning_effort_keys(rows)
    for task_rows in group_model_log_tasks(rows):
        ordered = sorted(
            task_rows,
            key=lambda row: (
                model_log_text(row.get("logged_at")),
                1 if model_log_row_is_retry(row) else 0,
            ),
        )
        first = ordered[0]
        final = ordered[-1]
        if model_log_row_is_reserved_fixture(final):
            continue
        group_engine = model_log_row_engine(final)
        group_model = model_log_row_model(final)
        group_task_type = model_log_row_task_type(final)
        unattributed = model_log_row_is_unattributed(final)
        reasoning_effort = None if unattributed else model_log_row_reasoning_effort(final)
        if model is not None and group_model != model:
            continue
        if task_type is not None and group_task_type != task_type:
            continue
        model_key = (group_engine, group_model, reasoning_effort or "", unattributed)
        model_entry = models.setdefault(
            model_key,
            {
                "engine": group_engine,
                "model": group_model,
                "reasoning_effort": reasoning_effort,
                "show_reasoning_effort": (
                    (group_engine, group_model, unattributed) in effort_keys
                ),
                "unattributed": unattributed,
                "tasks": 0,
                "attempts": 0,
                "passed": 0,
                "failed": 0,
                "retries": 0,
                "first_try_passed": 0,
                "last_seen": "",
                "_duration_ms": [],
                "_tokens": [],
                "_task_types": {},
            },
        )
        breakdown = model_entry["_task_types"].setdefault(
            group_task_type,
            {
                "task_type": group_task_type,
                "tasks": 0,
                "attempts": 0,
                "passed": 0,
                "failed": 0,
                "first_try_passed": 0,
                "last_seen": "",
            },
        )
        passed = model_log_text(final.get("verdict")).upper() == "PASS"
        first_passed = model_log_text(first.get("verdict")).upper() == "PASS"
        for target in (model_entry, breakdown):
            target["tasks"] += 1
            target["attempts"] += len(ordered)
            target["passed"] += 1 if passed else 0
            target["failed"] += 0 if passed else 1
            target["first_try_passed"] += 1 if first_passed else 0
            logged_at = model_log_text(final.get("logged_at"))
            if logged_at > target["last_seen"]:
                target["last_seen"] = logged_at
        model_entry["retries"] += max(0, len(ordered) - 1)
        duration_ms = model_log_int(final.get("duration_ms"))
        if duration_ms is not None:
            model_entry["_duration_ms"].append(duration_ms)
        for row in ordered:
            tokens = model_log_int(row.get("worker_tokens"))
            if tokens is not None:
                model_entry["_tokens"].append(tokens)

    finalized: list[dict[str, Any]] = []
    for entry in models.values():
        tasks_count = int(entry["tasks"])
        breakdown_rows = []
        for breakdown in entry["_task_types"].values():
            b_tasks = int(breakdown["tasks"])
            breakdown_rows.append(
                {
                    "task_type": breakdown["task_type"],
                    "tasks": b_tasks,
                    "attempts": breakdown["attempts"],
                    "passed": breakdown["passed"],
                    "failed": breakdown["failed"],
                    "first_try_pass_rate": breakdown["first_try_passed"] / b_tasks if b_tasks else 0.0,
                    "pass_rate": breakdown["passed"] / b_tasks if b_tasks else 0.0,
                    "last_seen": breakdown["last_seen"],
                }
            )
        breakdown_rows.sort(key=lambda item: (-item["tasks"], item["task_type"]))
        first_try_rate = entry["first_try_passed"] / tasks_count if tasks_count else 0.0
        tier = (
            "unranked"
            if entry["unattributed"]
            else model_scoreboard_tier(tasks_count, first_try_rate)
        )
        finalized.append(
            {
                "engine": entry["engine"],
                "model": entry["model"],
                "reasoning_effort": entry["reasoning_effort"],
                "show_reasoning_effort": entry["show_reasoning_effort"],
                "unattributed": entry["unattributed"],
                "tier": tier,
                "tasks": tasks_count,
                "attempts": entry["attempts"],
                "retries": entry["retries"],
                "passed": entry["passed"],
                "failed": entry["failed"],
                "first_try_pass_rate": entry["first_try_passed"] / tasks_count if tasks_count else 0.0,
                "pass_rate": entry["passed"] / tasks_count if tasks_count else 0.0,
                "median_duration_ms": median_int(entry["_duration_ms"]),
                "median_tokens": median_int(entry["_tokens"]),
                "last_seen": entry["last_seen"],
                "task_types": breakdown_rows,
            }
        )
    return finalized


def estimated_task_cost(row: dict[str, Any], catalog_model: dict[str, Any] | None) -> float | None:
    median_tokens = row.get("median_tokens")
    if median_tokens is None or catalog_model is None or catalog_model.get("variable_pricing"):
        return None
    if catalog_model.get("free"):
        return 0.0
    try:
        tokens = float(median_tokens)
        prompt_per_m = float(catalog_model.get("prompt_per_m") or 0)
        completion_per_m = float(catalog_model.get("completion_per_m") or 0)
    except (TypeError, ValueError):
        return None
    return tokens * ((prompt_per_m + completion_per_m) / 2.0) / 1_000_000


def model_sort_cost(row: dict[str, Any], catalog_model: dict[str, Any] | None) -> float:
    cost = estimated_task_cost(row, catalog_model)
    if cost is not None:
        return cost
    if row.get("median_tokens") is None:
        return 0.0
    return float("inf")


def order_model_scoreboard_rows(
    rows: list[dict[str, Any]],
    catalog_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            1 if row.get("unattributed") else 0,
            model_scoreboard_tier_rank(str(row.get("tier") or "")),
            -float(row.get("first_try_pass_rate") or 0),
            -float(row.get("pass_rate") or 0),
            model_sort_cost(row, catalog_by_id.get(str(row.get("model") or ""))),
            str(row.get("engine") or ""),
            str(row.get("model") or ""),
            str(row.get("reasoning_effort") or ""),
        ),
    )


def fmt_percent(value: Any) -> str:
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return "0%"


def fmt_int(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def fmt_task_cost(value: float | None) -> str:
    if value is None:
        return ""
    if value == 0:
        return "$0/task"
    if value < 0.01:
        return f"${value:.4f}/task"
    return f"${value:.2f}/task"


def fmt_short_task_cost(value: float | None) -> str:
    if value is None:
        return "in plan"
    if value == 0:
        return "free"
    if value < 0.10:
        cents = value * 100
        if cents < 1:
            return "<1¢"
        rounded = round(cents)
        return f"~{rounded}¢"
    return f"${value:.2f}"


def humanized_log_date(value: Any, *, prefix: str = "") -> str:
    text = model_log_text(value)
    if not text:
        return f"{prefix}unknown" if prefix else "unknown"
    candidate = text[:10]
    try:
        dt = datetime.strptime(candidate, "%Y-%m-%d")
    except ValueError:
        return f"{prefix}{text}" if prefix else text
    rendered = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    return f"{prefix}{rendered}" if prefix else rendered


def humanize_dates_in_text(value: str) -> str:
    return re.sub(
        r"\b\d{4}-\d{2}-\d{2}\b",
        lambda match: humanized_log_date(match.group(0)),
        value,
    )


def source_file_link(path: Path, label: str) -> str:
    resolved = path.expanduser().resolve()
    return (
        f'<a class="source-link" href="{html_escape(file_href(resolved))}" '
        f'title="{html_escape(str(resolved))}">{html_escape(label)}</a>'
    )


def model_task_cost_label(row: dict[str, Any], catalog_model: dict[str, Any] | None) -> str:
    median_tokens = row.get("median_tokens")
    if median_tokens is None:
        return "in plan"
    if catalog_model is None:
        return "catalog missing"
    if catalog_model.get("free"):
        return "free"
    if catalog_model.get("variable_pricing"):
        return "var"
    return fmt_short_task_cost(estimated_task_cost(row, catalog_model))


def rate_bar_html(value: Any) -> str:
    try:
        pct = max(0.0, min(100.0, float(value) * 100))
    except (TypeError, ValueError):
        pct = 0.0
    return (
        '<span class="rate-meter" aria-hidden="true">'
        f'<span class="rate-bar bar-fill" style="width: {pct:.0f}%"></span>'
        "</span>"
    )


def rate_cell_html(value: Any) -> str:
    return (
        f'<span class="rate-value">{html_escape(fmt_percent(value))}</span>'
        f"{rate_bar_html(value)}"
    )


def compact_context_label(value: Any) -> str:
    try:
        ctx = int(value)
    except (TypeError, ValueError):
        return "unknown ctx"
    if ctx >= 1_000_000 and ctx % 1_000_000 == 0:
        return f"{ctx // 1_000_000}M ctx"
    if ctx >= 1_000:
        if ctx % 1_000 == 0:
            return f"{ctx // 1_000}K ctx"
        return f"{ctx / 1000:.1f}K ctx"
    return f"{ctx} ctx"


def short_model_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    text = text.removeprefix("openrouter/")
    text = text.removesuffix(":free")
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    text = re.sub(r"[-_]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "unknown"
    parts = []
    for part in text.split():
        parts.append(part.upper() if part.lower() in {"gpt", "glm", "ai", "llm"} else part.capitalize())
    return " ".join(parts)


def catalog_model_display_name(model: dict[str, Any]) -> str:
    name = str(model.get("name") or "").strip()
    model_id = str(model.get("id") or "").strip()
    if name and name != model_id:
        return name.removesuffix(" (free)").strip()
    return short_model_name(model_id)


def humanized_short_date(value: Any) -> str:
    return re.sub(r",\s*\d{4}$", "", humanized_log_date(value))


def humanized_catalog_event_line(event: dict[str, Any], catalog_by_id: dict[str, dict[str, Any]]) -> str:
    model_id = str(event.get("id") or "")
    if model_id in catalog_by_id:
        label = catalog_model_display_name(catalog_by_id[model_id])
    else:
        label = short_model_name(model_id)
    kind = str(event.get("kind") or "event")
    date = humanized_short_date(event.get("ts"))
    if kind == "went_free":
        action = "went free"
    elif kind == "went_paid":
        action = "went paid"
    elif kind == "pricing_variable":
        action = "moved to variable pricing"
    elif kind == "pricing_fixed":
        action = "returned to fixed pricing"
    elif kind == "added":
        action = "was added"
    elif kind == "removed":
        action = "was removed"
    else:
        action = kind.replace("_", " ")
    return f"{label} {action} — {date}"


def watchlist_chip_html(model: dict[str, Any]) -> str:
    model_id = str(model.get("id") or "").strip()
    label = catalog_model_display_name(model)
    context = compact_context_label(model.get("context_length"))
    title = model_id or label
    return (
        '<li class="watch-chip">'
        f'<span data-model="{html_escape(model_id)}" title="{html_escape(title)}">'
        f"{html_escape(label)} · {html_escape(context)}</span></li>"
    )


def derived_quality_text(row: dict[str, Any], *, best: bool) -> str:
    candidates = [item for item in row.get("task_types", []) if int(item.get("tasks") or 0) >= 2]
    if not candidates:
        return "not enough per-task evidence yet"
    if best:
        chosen = max(candidates, key=lambda item: (float(item.get("first_try_pass_rate") or 0), int(item.get("tasks") or 0), str(item.get("task_type") or "")))
        prefix = "best derived"
    else:
        chosen = min(candidates, key=lambda item: (float(item.get("first_try_pass_rate") or 0), -int(item.get("tasks") or 0), str(item.get("task_type") or "")))
        prefix = "worst derived"
    return (
        f"{prefix}: {chosen['task_type']} "
        f"({fmt_percent(chosen.get('first_try_pass_rate'))} first-try, "
        f"{fmt_percent(chosen.get('pass_rate'))} pass, n={fmt_int(chosen.get('tasks'))})"
    )


def render_task_breakdown_table(task_rows: list[dict[str, Any]]) -> str:
    if not task_rows:
        return '<p class="empty-note">no task-type breakdown</p>'
    rows = []
    for item in task_rows:
        rows.append(
            f"""<tr>
      <td>{html_escape(str(item.get("task_type") or ""))}</td>
      <td class="num">{fmt_int(item.get("tasks"))}</td>
      <td class="num rate-cell">{rate_cell_html(item.get("first_try_pass_rate"))}</td>
      <td class="num rate-cell">{rate_cell_html(item.get("pass_rate"))}</td>
    </tr>"""
        )
    return f"""<table class="breakdown">
    <thead><tr><th>task_type</th><th>n</th><th>first-try</th><th>pass</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table>"""


MODEL_SCOREBOARD_CSS = """
  .scoreboard-page { max-width: 1240px; }
  .scoreboard-header {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: 18px;
    align-items: end;
    margin-bottom: clamp(14px, 2.5vw, 22px);
  }
  .scoreboard-title {
    margin: 0;
    font-size: clamp(28px, 5vw, 54px);
    line-height: .98;
    letter-spacing: 0;
    text-wrap: balance;
  }
  .scoreboard-meta {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 12px;
    flex-wrap: wrap;
    color: var(--muted);
    font-size: 12px;
  }
  .source-links {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex-wrap: wrap;
  }
  .source-link {
    color: var(--ink);
    text-decoration: none;
    border-bottom: 1px solid var(--hairline);
  }
  .source-link:hover { border-bottom-color: var(--ink); }
  .watchlist {
    border-top: 1px solid var(--hairline);
    border-bottom: 1px solid var(--hairline);
    padding: 12px 0;
    margin: 0 0 clamp(18px, 3vw, 28px);
    color: var(--muted);
    font-size: 13px;
  }
  .watchline {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-wrap: wrap;
  }
  .watch-title { color: var(--ink); font-weight: 650; }
  .watch-chips, .event-list {
    display: contents;
    margin: 0;
    padding: 0;
    list-style: none;
  }
  .watch-chip {
    border: 1px solid var(--hairline);
    padding: 3px 7px 4px;
    font-size: 12px;
    color: var(--ink);
    background: rgba(255, 255, 255, .03);
  }
  .event-list li {
    color: var(--muted);
  }
  .event-list li::before {
    content: "/";
    color: var(--hairline);
    margin: 0 8px 0 2px;
  }
  .table-scroll {
    overflow-x: auto;
    border: 1px solid var(--hairline);
    border-left: 0;
    border-right: 0;
  }
  .ranked-table {
    width: 100%;
    min-width: 1040px;
    border-collapse: collapse;
    font-size: 13px;
  }
  .ranked-table th {
    color: var(--muted);
    font-size: 11px;
    font-weight: 650;
    letter-spacing: .08em;
    text-transform: uppercase;
    text-align: left;
    padding: 10px 12px;
    border-bottom: 1px solid var(--hairline);
    white-space: nowrap;
  }
  .ranked-table td {
    padding: 17px 12px;
    border-bottom: 1px solid var(--hairline);
    vertical-align: middle;
    color: var(--ink);
  }
  .ranked-table tr.model-row:hover td {
    background: color-mix(in srgb, var(--surface) 48%, transparent);
  }
  .rank-cell {
    width: 58px;
    font-size: 16px;
    font-weight: 760;
  }
  .model-cell { min-width: 230px; }
  .model-name {
    font-size: 16px;
    font-weight: 760;
    line-height: 1.2;
    overflow-wrap: anywhere;
  }
  .model-id {
    margin-top: 3px;
    color: var(--muted);
    font-size: 12px;
    overflow-wrap: anywhere;
  }
  .identity-flag, .verified-date {
    display: block;
    margin-top: 3px;
    color: var(--muted);
    font-size: 11px;
  }
  .tier-badge {
    display: inline-flex;
    align-items: center;
    min-width: 78px;
    justify-content: center;
    padding: 3px 8px 4px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 700;
    color: var(--ink);
    border: 1px solid transparent;
  }
  .tier-badge.proven {
    background: color-mix(in srgb, var(--pass) 30%, transparent);
    border-color: color-mix(in srgb, var(--pass) 58%, var(--hairline));
  }
  .tier-badge.probation {
    background: color-mix(in srgb, var(--accent) 24%, transparent);
    border-color: color-mix(in srgb, var(--accent) 48%, var(--hairline));
  }
  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }
  .rate-cell {
    min-width: 108px;
  }
  .rate-value {
    display: inline-block;
    min-width: 38px;
  }
  .rate-meter {
    display: inline-block;
    width: 48px;
    height: 6px;
    margin-left: 8px;
    vertical-align: 1px;
    border-radius: 4px;
    background: color-mix(in srgb, var(--muted) 22%, transparent);
    overflow: hidden;
  }
  .rate-bar {
    display: block;
    height: 100%;
    border-radius: 4px;
    background: var(--pass);
  }
  .detail-row td {
    padding: 0;
    background: color-mix(in srgb, var(--surface) 55%, transparent);
  }
  .model-detail {
    padding: 0;
  }
  .model-detail summary {
    cursor: pointer;
    color: var(--ink);
    padding: 11px 12px;
    font-size: 13px;
    border-bottom: 1px solid var(--hairline);
  }
  .model-detail summary:hover { background: color-mix(in srgb, var(--surface) 70%, transparent); }
  .detail-content {
    display: grid;
    grid-template-columns: minmax(0, .86fr) minmax(260px, 1.14fr);
    gap: 22px;
    padding: 16px 12px 20px;
  }
  .detail-heading {
    margin: 0 0 8px;
    color: var(--muted);
    font-size: 11px;
    font-weight: 650;
    letter-spacing: .08em;
    text-transform: uppercase;
  }
  .quality-lines {
    display: grid;
    gap: 6px;
    margin: 12px 0 0;
    color: var(--muted);
    font-size: 13px;
  }
  .quality-lines b { color: var(--ink); }
  .notes-panel {
    min-width: 0;
  }
  .breakdown {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  .breakdown th, .breakdown td {
    border-bottom: 1px solid var(--hairline);
    padding: 8px 6px;
    text-align: left;
  }
  .breakdown th {
    color: var(--muted);
    font-weight: 650;
  }
  .notes-list {
    display: grid;
    gap: 10px;
    margin: 0;
    padding: 0;
    list-style: none;
    color: var(--ink);
  }
  .notes-list li {
    display: grid;
    grid-template-columns: 72px minmax(0, 1fr);
    gap: 12px;
    align-items: baseline;
  }
  .notes-list time {
    color: var(--muted);
    font-size: 11px;
    font-variant-caps: all-small-caps;
    letter-spacing: .08em;
    white-space: nowrap;
  }
  .more-notes {
    color: var(--muted);
    font-size: 12px;
  }
  .empty-note { color: var(--muted); margin: 0; }
  .muted { color: var(--muted); }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace; }
  footer.scoreboard-footer {
    margin-top: 14px;
    color: var(--muted);
    font-size: 12px;
  }
  @media (prefers-color-scheme: dark) {
    .watch-chip { background: rgba(255, 255, 255, .035); }
  }
  @media (max-width: 820px) {
    body { padding: 18px 14px; }
    .scoreboard-header, .detail-content { grid-template-columns: 1fr; }
    .scoreboard-meta { justify-content: flex-start; }
    .table-scroll { margin-left: -14px; margin-right: -14px; padding-left: 14px; }
    .notes-list li { grid-template-columns: 1fr; gap: 2px; }
  }
"""


def render_free_watchlist(
    catalog_models: list[dict[str, Any]],
    catalog_path: Path,
    *,
    events: list[dict[str, Any]] | None = None,
) -> str:
    normalized = [normalize_catalog_for_scoreboard(model) for model in catalog_models]
    catalog_by_id = catalog_models_by_id(normalized)
    free_models = sorted(
        (model for model in normalized if model.get("free")),
        key=lambda item: (-int(item.get("context_length") or 0), str(item.get("id") or "")),
    )
    if free_models:
        free_items = "".join(
            watchlist_chip_html(model)
            for model in free_models[:6]
        )
    else:
        free_items = '<li class="muted">no free catalog models in the current snapshot</li>'
    if events is None:
        events = read_catalog_events(catalog_changes_path(catalog_path), limit=6)
    event_items = "".join(
        f"<li>{html_escape(humanized_catalog_event_line(event, catalog_by_id))}</li>"
        for event in events[:3]
    )
    if not event_items:
        event_items = '<li class="muted">no catalog change log found</li>'
    return f"""<section class="watchlist" aria-label="free model watchlist">
    <div class="watchline">
      <span class="watch-title">{fmt_int(len(free_models))} models free on OpenRouter right now</span>
      <ul class="watch-chips">{free_items}</ul>
      <ul class="event-list">{event_items}</ul>
    </div>
  </section>"""


def render_model_table_pair(
    row: dict[str, Any],
    *,
    rank: int | None,
    catalog_model: dict[str, Any] | None,
    notes_sections: dict[str, list[str]],
    notes_path: Path,
) -> str:
    model_id = str(row.get("model") or "")
    model_display = str(row.get("model_display") or model_id)
    lab = str(row.get("lab") or "(unknown)")
    harness = str(row.get("harness") or "unknown")
    access = str(row.get("access") or "unknown")
    last_verified = str(row.get("last_verified") or "")
    notes = model_judgment_notes(model_id, notes_sections)
    if row.get("unattributed"):
        model_id_line = (
            f'<div class="model-id">engine: {html_escape(str(row.get("engine") or "unknown"))} '
            '· quarantined legacy data</div>'
        )
    else:
        model_id_line = "" if model_id == model_display else f'<div class="model-id">{html_escape(model_id)}</div>'
    tier = str(row.get("tier") or "")
    tier_display = "not ranked" if row.get("unattributed") else tier
    rank_display = "—" if rank is None else str(rank)
    row_id = (
        str(row.get("bucket_id") or model_id)
        if row.get("show_reasoning_effort") or row.get("unattributed")
        else model_id
    )
    unregistered_flag = (
        '<span class="identity-flag">unregistered</span>' if row.get("unregistered") else ""
    )
    verified_date = (
        f'<span class="verified-date">verified {html_escape(last_verified)}</span>'
        if last_verified
        else ""
    )
    return f"""<tr class="model-row" id="model-{html_escape(sanitize_artifact_name(row_id))}">
      <td class="rank-cell num">{rank_display}</td>
      <td class="model-cell"><div class="model-name">{html_escape(model_display)}</div>{model_id_line}{unregistered_flag}</td>
      <td>{html_escape(lab)}{verified_date}</td>
      <td>{html_escape(harness)}</td>
      <td>{html_escape(access)}</td>
      <td><span class="tier-badge {html_escape(tier)}">{html_escape(tier_display)}</span></td>
      <td class="num">{fmt_int(row.get("tasks"))}</td>
      <td class="num rate-cell">{rate_cell_html(row.get("first_try_pass_rate"))}</td>
      <td class="num rate-cell">{rate_cell_html(row.get("pass_rate"))}</td>
      <td class="num">{html_escape(model_task_cost_label(row, catalog_model))}</td>
      <td>{html_escape(humanized_log_date(row.get("last_seen")))}</td>
    </tr>
    <tr class="detail-row">
      <td colspan="11">
        <details class="model-detail">
          <summary>details for {html_escape(model_display)}</summary>
          <div class="detail-content">
            <div>
              <h3 class="detail-heading">Task types</h3>
              {render_task_breakdown_table(row.get("task_types", []))}
              <div class="quality-lines">
                <div><b>Best:</b> {html_escape(derived_quality_text(row, best=True))}</div>
                <div><b>Worst:</b> {html_escape(derived_quality_text(row, best=False))}</div>
              </div>
            </div>
            <div class="notes-panel">
              <h3 class="detail-heading">Judgment notes</h3>
              {render_notes_list(notes, notes_path=notes_path)}
            </div>
          </div>
        </details>
      </td>
    </tr>"""


def render_model_scoreboard_html(
    *,
    rows: list[dict[str, Any]],
    log_path: Path,
    rows_read: int,
    skipped: int,
    catalog_path: Path,
    catalog_models: list[dict[str, Any]],
    notes_path: Path,
    notes_sections: dict[str, list[str]],
    catalog_events: list[dict[str, Any]] | None = None,
    generated_at: str | None = None,
) -> str:
    catalog_by_id = catalog_models_by_id(catalog_models)
    ordered = order_model_scoreboard_rows(rows, catalog_by_id)
    generated = generated_at or datetime.now().astimezone().replace(microsecond=0).isoformat()
    rendered_rows: list[str] = []
    rank = 0
    for row in ordered:
        row_rank = None
        if not row.get("unattributed"):
            rank += 1
            row_rank = rank
        rendered_rows.append(
            render_model_table_pair(
                row,
                rank=row_rank,
                catalog_model=catalog_by_id.get(str(row.get("model") or "")),
                notes_sections=notes_sections,
                notes_path=notes_path,
            )
        )
    table_rows = "".join(rendered_rows)
    if not table_rows:
        table_rows = '<tr><td colspan="11" class="muted">No local model evidence matched these filters.</td></tr>'
    unregistered_slugs = sorted(
        {str(row.get("model") or "") for row in ordered if row.get("unregistered") and row.get("model")}
    )
    unregistered_pointer = ""
    if unregistered_slugs:
        pointer = (
            f"Unregistered model slug(s): {', '.join(unregistered_slugs)} "
            "— run the identity procedure in docs/TAXONOMY.md."
        )
        unregistered_pointer = f'<div class="identity-pointer">{html_escape(pointer)}</div>'
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
{CSP_META_TAG}
<title>ringer model scoreboard</title>
<style>{ARTIFACT_BASE_CSS}
{MODEL_SCOREBOARD_CSS}</style>
</head>
<body>
<div class="page scoreboard-page">
  <header class="scoreboard-header">
    <h1 class="scoreboard-title">Model performance scoreboard</h1>
    <div class="scoreboard-meta">
      <span>Generated {html_escape(humanized_log_date(generated))}</span>
      <nav class="source-links" aria-label="scoreboard source files">
        {source_file_link(log_path, "eval log")}
        {source_file_link(catalog_path, "catalog")}
        {source_file_link(notes_path, "model notes")}
      </nav>
    </div>
  </header>
  {render_free_watchlist(catalog_models, catalog_path, events=catalog_events)}
  <main>
    <div class="table-scroll">
      <table class="ranked-table">
        <thead>
          <tr>
            <th>Rank</th>
            <th>Model</th>
            <th>Lab</th>
            <th>Harness</th>
            <th>API/Plan</th>
            <th>Tier</th>
            <th class="num">Tasks</th>
            <th class="num">First-try</th>
            <th class="num">Pass</th>
            <th class="num">Est. $/task</th>
            <th>Last used</th>
          </tr>
        </thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
  </main>
  <footer class="scoreboard-footer">
    <span>{fmt_int(rows_read)} rows read, {fmt_int(skipped)} skipped lines. Ranking sorts by evidence tier first: proven n&gt;=3, then probation; ties use first-try pass rate, pass rate, then lower estimated cost. Unattributed legacy rows are quarantined at the bottom and are not ranked or tiered. Cost estimate assumes logged worker_tokens are split 50/50 between prompt and completion tokens, using the catalog $/M in/out blend.</span>
    {unregistered_pointer}
  </footer>
</div>
</body>
</html>
"""


def write_model_scoreboard_html(
    config: AppConfig,
    *,
    path: Path | None,
    rows: list[dict[str, Any]],
    log_path: Path,
    rows_read: int,
    skipped: int,
    catalog_path: Path,
    catalog_models: list[dict[str, Any]],
    notes_path: Path,
    notes_sections: dict[str, list[str]],
    catalog_events: list[dict[str, Any]] | None = None,
) -> Path:
    target = path
    if target is None:
        target = artifact_live_path(config.state_dir, MODEL_SCOREBOARD_RUN_NAME)
    target = target.expanduser().resolve()
    html = render_model_scoreboard_html(
        rows=rows,
        log_path=log_path,
        rows_read=rows_read,
        skipped=skipped,
        catalog_path=catalog_path,
        catalog_models=catalog_models,
        catalog_events=catalog_events,
        notes_path=notes_path,
        notes_sections=notes_sections,
    )
    atomic_write_text(target, html)
    if path is None:
        update_artifact_library_live(
            config.state_dir,
            run_name=MODEL_SCOREBOARD_RUN_NAME,
            run_id=MODEL_SCOREBOARD_RUN_NAME,
            identity=MODEL_SCOREBOARD_IDENTITY,
            state="pass",
        )
    return target


def print_model_log_table(path: Path, rows_read: int, skipped: int, groups: list[dict[str, Any]]) -> None:
    print(f"Model log: {path} ({rows_read} rows, {skipped} skipped lines)")
    header = (
        f"{'task_type':<18} {'model':<32} {'lab':<20} {'harness':<16} {'tasks':>5} "
        f"{'attempts':>8} {'passed':>6} {'failed':>6} {'pass':>6} "
        f"{'first':>6} {'dur_ms':>8} {'tokens':>8} {'last_seen'}"
    )
    print(header)
    print("-" * len(header))
    for group in groups:
        duration = "" if group["median_duration_ms"] is None else str(group["median_duration_ms"])
        tokens = "" if group["median_tokens"] is None else str(group["median_tokens"])
        display = str(group.get("model_display") or group["model"])
        if group.get("unattributed"):
            display = f"{display} [{group.get('engine') or 'unknown'}]"
        elif display != group["model"]:
            display = f"{display} ({group['model']})"
        if group.get("unregistered"):
            display = f"{display} [unregistered]"
        print(
            f"{group['task_type']:<18} {shorten(display, 32):<32} "
            f"{shorten(str(group.get('lab') or '(unknown)'), 20):<20} "
            f"{shorten(str(group.get('harness') or 'unknown'), 16):<16} "
            f"{group['tasks']:>5} {group['attempts']:>8} {group['passed']:>6} "
            f"{group['failed']:>6} {group['pass_rate']:>6.2f} "
            f"{group['first_try_pass_rate']:>6.2f} {duration:>8} "
            f"{tokens:>8} {group['last_seen']}"
        )
    print("Judgment layer: docs/MODEL-NOTES.md")
    unregistered_slugs = sorted(
        {str(group.get("model") or "") for group in groups if group.get("unregistered") and group.get("model")}
    )
    if unregistered_slugs:
        print(
            f"Unregistered model slug(s): {', '.join(unregistered_slugs)} "
            "— run the identity procedure in docs/TAXONOMY.md."
        )


def build_models_api_payload(
    *,
    log_path: Path,
    default_log_path: Path | None = None,
    db_path: Path | None = None,
    catalog_path: Path | None = None,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    log_path = log_path.expanduser().resolve()
    default_log_path = (default_log_path or log_path).expanduser().resolve()
    explicit_db = db_path is not None
    resolved_db_path = (db_path or default_read_model_db_path()).expanduser().resolve()
    catalog_path = (catalog_path or default_catalog_path()).expanduser().resolve()
    registry_path = (registry_path or default_model_registry_path()).expanduser().resolve()
    using_db = should_use_read_model_db(
        log_path=log_path,
        default_log_path=default_log_path,
        explicit_db=explicit_db,
    )
    catalog_models: list[dict[str, Any]] = []
    if using_db:
        try:
            sync_read_model_db(
                resolved_db_path,
                log_path,
                catalog_path=catalog_path,
                registry_path=registry_path,
            )
            rows, identity_registry = db_attempt_rows(resolved_db_path)
            catalog_models = db_catalog_models(resolved_db_path)
        except Exception:
            using_db = False
            rows, _skipped = read_model_log_rows(log_path)
            identity_registry = load_model_identity_registry(registry_path)
    else:
        rows, _skipped = read_model_log_rows(log_path)
        identity_registry = load_model_identity_registry(registry_path)
    if not using_db:
        with contextlib.suppress(Exception):
            catalog_models = load_catalog_snapshot(catalog_path)
    groups = enrich_model_groups_with_identity(
        aggregate_model_log_rows(rows),
        rows,
        identity_registry,
        include_task_type=True,
        catalog_models=catalog_models,
    )
    rollup = enrich_model_groups_with_identity(
        aggregate_model_scoreboard_rows(rows),
        rows,
        identity_registry,
        include_task_type=False,
        catalog_models=catalog_models,
    )
    catalog_by_id = catalog_models_by_id(catalog_models)
    ordered_rollup: list[dict[str, Any]] = []
    for row in order_model_scoreboard_rows(rollup, catalog_by_id):
        item = dict(row)
        item.setdefault("task_type", "(all)")
        ordered_rollup.append(item)
    return {
        "generated_at": utc_now_iso(),
        "groups": groups,
        "rollup": ordered_rollup,
    }


def run_models_command(config: AppConfig, args: argparse.Namespace) -> int:
    default_log_path = config.eval.jsonl_path.expanduser().resolve()
    log_path = (args.log or default_log_path).expanduser().resolve()
    since = validate_since_date(args.since)
    explicit_db = getattr(args, "db", None) is not None
    db_path = (getattr(args, "db", None) or default_read_model_db_path()).expanduser().resolve()
    catalog_path = (getattr(args, "catalog_file", None) or default_catalog_path()).expanduser().resolve()
    registry_path = (getattr(args, "registry", None) or default_model_registry_path()).expanduser().resolve()
    using_db = should_use_read_model_db(
        log_path=log_path,
        default_log_path=default_log_path,
        explicit_db=explicit_db,
    )
    catalog_events: list[dict[str, Any]] | None = None
    if using_db:
        try:
            sync_result = sync_read_model_db(
                db_path,
                log_path,
                catalog_path=catalog_path,
                registry_path=registry_path,
            )
            rows, identity_registry = db_attempt_rows(db_path, since=since, engine=args.engine)
            skipped = sync_result.skipped
            catalog_models_from_db = db_catalog_models(db_path)
            catalog_events = db_catalog_events(db_path, limit=6)
        except Exception as exc:
            using_db = False
            print(f"models: SQLite read model unavailable; using JSONL fallback ({exc})", file=sys.stderr)
            rows, skipped = read_model_log_rows(log_path, since=since, engine=args.engine)
            identity_registry = load_model_identity_registry(registry_path)
            catalog_models_from_db = []
    else:
        rows, skipped = read_model_log_rows(log_path, since=since, engine=args.engine)
        identity_registry = load_model_identity_registry(registry_path)
        catalog_models_from_db = []
    catalog_models = catalog_models_from_db if using_db else load_catalog_snapshot(catalog_path)
    groups = enrich_model_groups_with_identity(
        aggregate_model_log_rows(rows, task_type=args.task_type, model=args.model),
        rows,
        identity_registry,
        include_task_type=True,
        catalog_models=catalog_models,
    )
    if args.explore:
        print_model_explore(
            log_path=log_path,
            rows_read=len(rows),
            skipped=skipped,
            groups=groups,
            catalog_path=catalog_path,
            catalog_models=catalog_models,
        )
        return 0
    html_arg = getattr(args, "html", None)
    open_requested = bool(getattr(args, "open", False))
    if html_arg is not None or open_requested:
        notes_path = (getattr(args, "notes_file", None) or default_model_notes_path()).expanduser().resolve()
        scoreboard_rows = enrich_model_groups_with_identity(
            aggregate_model_scoreboard_rows(rows, task_type=args.task_type, model=args.model),
            rows,
            identity_registry,
            include_task_type=False,
            catalog_models=catalog_models,
        )
        explicit_path = None
        if html_arg not in {None, ""}:
            explicit_path = Path(str(html_arg))
        page_path = write_model_scoreboard_html(
            config,
            path=explicit_path,
            rows=scoreboard_rows,
            log_path=log_path,
            rows_read=len(rows),
            skipped=skipped,
            catalog_path=catalog_path,
            catalog_models=catalog_models,
            notes_path=notes_path,
            notes_sections=parse_model_notes_sections(notes_path),
            catalog_events=catalog_events,
        )
        print(page_path)
        if open_requested:
            open_in_browser(file_href(page_path))
        return 0
    if args.json:
        print(json.dumps(groups))
    else:
        print_model_log_table(log_path, len(rows), skipped, groups)
    return 0


class Verifier:
    async def verify(self, task: TaskSpec, taskdir: Path) -> VerifyResult:
        if taskdir.is_dir():
            check_returncode, check_timed_out, output = await self._run_check(task.check, taskdir)
        else:
            # A worker can delete or replace its own taskdir (seen live
            # 2026-07-09); spawning the check there raises ENOENT, and
            # unhandled it kills every other lane in the run. Fail the task.
            check_returncode, check_timed_out = None, False
            output = (
                f"[ringer] task dir missing at verification: {taskdir} — "
                "the worker deleted or replaced its own working directory"
            )
        missing_files = tuple(
            rel for rel in task.expect_files if not self._is_nonempty_file(self._expect_file_path(taskdir, rel))
        )
        ok = not missing_files and not check_timed_out and check_returncode == 0
        if missing_files:
            missing_message = f"[ringer] missing expected files: {', '.join(missing_files)}"
            output = f"{missing_message}\n{output}" if output.strip() else missing_message
        elif not check_timed_out and check_returncode != 0 and not output.strip():
            # A silent failing check wastes the retry (no failure context to
            # inject) and blinds the eval row. Say so, in both places.
            output = (
                f"[ringer] check failed silently (exit {check_returncode}, no output). "
                "Prefer checks that print WHY they fail — the retry prompt and the "
                "eval log both depend on it."
            )
        return VerifyResult(
            ok=ok,
            check_returncode=check_returncode,
            check_timed_out=check_timed_out,
            raw_output_excerpt=output[:2000],
            missing_files=missing_files,
        )

    @staticmethod
    def _is_nonempty_file(path: Path) -> bool:
        try:
            return path.is_file() and path.stat().st_size > 0
        except OSError:
            return False

    @staticmethod
    def _expect_file_path(taskdir: Path, path: str) -> Path:
        candidate = Path(path).expanduser()
        # Keep runtime verification aligned with lint's treatment of "~" paths.
        return candidate if candidate.is_absolute() else taskdir / candidate

    @staticmethod
    async def _run_check(command: str, cwd: Path) -> tuple[int | None, bool, str]:
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            # The taskdir can still vanish between verify()'s guard and this
            # spawn; a check that cannot start is a task FAIL, never a run crash.
            return None, False, f"[ringer] check spawn failed: {exc}"
        timed_out = False
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CHECK_TIMEOUT_S)
        except asyncio.TimeoutError:
            timed_out = True
            terminate_process_group(proc)
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            except asyncio.TimeoutError:
                kill_process_group(proc)
                stdout, _ = await proc.communicate()
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        if timed_out:
            output += f"\n[ringer.py] check timed out after {CHECK_TIMEOUT_S}s\n"
        return proc.returncode, timed_out, output


class RingerRunner:
    def __init__(
        self,
        manifest: Manifest,
        config: AppConfig,
        identity: str,
        dashboard_enabled: bool = True,
        force_browser: bool = False,
    ) -> None:
        self.manifest = manifest
        self.config = config
        self.identity = identity
        self.dashboard_enabled = dashboard_enabled
        self.run_id = build_run_id(manifest.run_name)
        self.started_at = datetime.now(timezone.utc)
        self.lock = threading.RLock()
        self.runtimes = [self._task_runtime(task) for task in manifest.tasks]
        self.state_writer = StateWriter(
            self.run_id,
            manifest.run_name,
            identity,
            config.state_dir,
            config.engines,
            self.started_at,
            self.runtimes,
            self.lock,
            max_parallel=manifest.max_parallel,
            artifact=config.artifact,
        )
        self.dashboard = (
            Dashboard(
                state_path=self.state_writer.path,
                preferred_port=config.dashboard_port_base,
                hud_app_path=config.hud_app_path,
                force_browser=force_browser,
            )
            if dashboard_enabled
            else None
        )
        self.logger = EvalLogger(config.eval)
        self.verifier = Verifier()
        self.semaphore = asyncio.Semaphore(manifest.max_parallel)
        self.active_processes: dict[int, asyncio.subprocess.Process] = {}

    async def run(self) -> int:
        self.manifest.workdir.mkdir(parents=True, exist_ok=True)
        final_state = False
        try:
            self.state_writer.start()
            if self.dashboard is not None:
                self.state_writer.set_port(self.dashboard.start())
            await asyncio.gather(*(self._run_task(runtime) for runtime in self.runtimes))
            final_state = True
            return 0 if all(runtime.status == "pass" for runtime in self.runtimes) else 1
        except asyncio.CancelledError:
            await self.kill_all_workers()
            with self.lock:
                now = time.monotonic()
                for runtime in self.runtimes:
                    if runtime.status not in {"pass", "fail"}:
                        runtime.status = "fail"
                        runtime.final_verdict = "ERROR"
                        runtime.ended_at_monotonic = runtime.ended_at_monotonic or now
            self.state_writer.flush()
            final_state = True
            raise
        finally:
            if final_state:
                self.state_writer.finish()
            self.state_writer.stop()
            if self.dashboard is not None:
                self.dashboard.stop()
            self.logger.close()
            print_summary(self.run_id, self.runtimes)
            print("Model log updated; run './ringer.py models' for the per-model scoreboard.")
            # The post-run journey: tell a human exactly where the results live.
            with contextlib.suppress(Exception):
                if self.state_writer.artifact is not None and self.state_writer.artifact.enabled:
                    results_page = artifact_live_path(self.state_writer.state_dir, self.manifest.run_name)
                    print(f"\nYour results: {results_page}")
                    print("Open it in a browser, or run './ringer.py hud' for the full Ringside view (http://127.0.0.1:8700).")

    async def kill_all_workers(self) -> None:
        procs = list(self.active_processes.values())
        for proc in procs:
            if proc.returncode is None:
                terminate_process_group(proc)
        if procs:
            await asyncio.sleep(1)
        for proc in procs:
            if proc.returncode is None:
                kill_process_group(proc)

    async def _run_task(self, runtime: TaskRuntime) -> None:
        async with self.semaphore:
            with self.lock:
                runtime.started_at_monotonic = time.monotonic()
            prepared, prepare_error = await self._prepare_taskdir(runtime)
            if not prepared:
                await self._record_prepare_error(runtime, prepare_error or "taskdir preparation failed")
                return
            current_spec = runtime.task.spec
            max_attempts = 2
            for attempt in range(1, max_attempts + 1):
                retrying = attempt > 1
                with self.lock:
                    runtime.attempts = attempt
                    runtime.status = "retrying" if retrying else "running"
                attempt_started = time.monotonic()
                worker = await self._run_worker(runtime, current_spec, attempt)
                with self.lock:
                    runtime.worker_pid = None
                    runtime.status = "verifying"
                    if worker.tokens is not None:
                        runtime.tokens = (runtime.tokens or 0) + worker.tokens
                verify = await self.verifier.verify(runtime.task, runtime.taskdir)
                verdict = verdict_for(worker, verify)
                with self.lock:
                    runtime.last_check_returncode = verify.check_returncode
                    runtime.last_check_timed_out = verify.check_timed_out
                    runtime.last_check_output = verify.raw_output_excerpt
                duration_ms = int((time.monotonic() - attempt_started) * 1000)
                self._log_attempt(runtime, current_spec, retrying, worker, verify, verdict, duration_ms)
                if verdict == "PASS":
                    self._harvest_deliverables_on_pass(runtime)
                    with self.lock:
                        runtime.status = "pass"
                        runtime.final_verdict = verdict
                        runtime.ended_at_monotonic = time.monotonic()
                    await self._cleanup_worktree_on_pass(runtime)
                    return
                # A vanished taskdir cannot host a retry — the respawn would
                # only ENOENT — so let this FAIL stand as final.
                if attempt < max_attempts and verdict in {"FAIL", "TIMEOUT"} and runtime.taskdir.is_dir():
                    failure_context = build_failure_context(runtime.log_path, verify.raw_output_excerpt)
                    current_spec = (
                        f"{runtime.task.spec}\n\n"
                        f"Previous attempt failed: {failure_context}. Fix it."
                    )
                    continue
                with self.lock:
                    runtime.status = "fail"
                    runtime.final_verdict = verdict
                    runtime.ended_at_monotonic = time.monotonic()
                return

    def _harvest_deliverables_on_pass(self, runtime: TaskRuntime) -> None:
        harvested: list[dict[str, Any]] = []
        notes: list[str] = []
        target_dir = artifact_deliverables_dir(
            self.config.state_dir,
            self.run_id,
            runtime.task.key,
        )
        expect_files: tuple[str, ...] = runtime.task.expect_files
        worktree_task = self.manifest.worktrees and self.manifest.repo is not None
        if not expect_files and not worktree_task:
            # (In worktrees mode the taskdir root is a whole repo checkout —
            # guessing there would harvest README.md and friends, not work.)
            # No declared deliverables: rescue what the worker left at the
            # top of its task directory, or the run's real output (a review,
            # a report) never reaches the results page. Declaring
            # expect_files remains the way to control exactly what is shown.
            candidates = sorted(
                path.name
                for path in runtime.taskdir.glob("*")
                if path.is_file()
                and not path.name.startswith(".")
                and path.suffix.lower() in FALLBACK_HARVEST_SUFFIXES
            )
            if len(candidates) > FALLBACK_HARVEST_MAX_FILES:
                notes.append(
                    f"Only the first {FALLBACK_HARVEST_MAX_FILES} of {len(candidates)} files were "
                    "collected automatically; declare expect_files to choose exactly what is kept."
                )
                candidates = candidates[:FALLBACK_HARVEST_MAX_FILES]
            expect_files = tuple(candidates)
        for expect_path in expect_files:
            source = Verifier._expect_file_path(runtime.taskdir, expect_path)
            try:
                stat = source.stat()
            except OSError:
                continue
            if not source.is_file():
                continue
            if stat.st_size > DELIVERABLE_MAX_BYTES:
                notes.append(
                    f"{source.name} was not copied because it is larger than 20 MB "
                    f"({stat.st_size:,} bytes)."
                )
                continue
            target = target_dir / source.name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied_size = target.stat().st_size
            except OSError as exc:
                append_text(
                    runtime.log_path,
                    f"[ringer.py] deliverable copy failed for {source.name}: {exc}\n",
                )
                continue
            harvested.append({"name": source.name, "path": str(target), "bytes": copied_size})
        if harvested or notes:
            with self.lock:
                runtime.deliverables = harvested
                runtime.deliverable_notes.extend(notes)

    async def _prepare_taskdir(self, runtime: TaskRuntime) -> tuple[bool, str | None]:
        taskdir = runtime.taskdir
        if self.manifest.worktrees and self.manifest.repo is not None:
            taskdir.parent.mkdir(parents=True, exist_ok=True)
            if taskdir.exists():
                return False, f"worktree taskdir already exists: {taskdir}"
            proc = await asyncio.create_subprocess_exec(
                "git",
                "-C",
                str(self.manifest.repo),
                "worktree",
                "add",
                str(taskdir),
                "HEAD",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode != 0:
                message = stdout.decode("utf-8", errors="replace")
                append_text(runtime.log_path, f"[ringer.py] git worktree add failed:\n{message}\n")
                return False, message.strip() or "git worktree add failed"
            return True, None
        taskdir.mkdir(parents=True, exist_ok=True)
        return True, None

    async def _cleanup_worktree_on_pass(self, runtime: TaskRuntime) -> None:
        if not (self.manifest.worktrees and self.manifest.repo is not None):
            return
        self._snapshot_worktree_reports(runtime)
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(self.manifest.repo),
            "worktree",
            "remove",
            "--force",
            str(runtime.taskdir),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            message = stdout.decode("utf-8", errors="replace")
            append_text(runtime.log_path, f"[ringer.py] git worktree remove failed:\n{message}\n")

    def _snapshot_worktree_reports(self, runtime: TaskRuntime) -> None:
        copied: dict[str, Path] = {}
        report_dir = (runtime.log_path.parent / f"{runtime.log_path.stem}.reports").resolve()
        for report_name in TASK_REPORT_FILENAMES:
            source = runtime.taskdir / report_name
            if not source.exists():
                continue
            target = report_dir / report_name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            except OSError as exc:
                append_text(
                    runtime.log_path,
                    f"[ringer.py] report snapshot failed for {report_name}: {exc}\n",
                )
                continue
            copied[report_name] = target
        if copied:
            with self.lock:
                runtime.report_paths.update(copied)

    async def _record_prepare_error(self, runtime: TaskRuntime, error: str) -> None:
        with self.lock:
            runtime.attempts = 1
            runtime.status = "fail"
            runtime.final_verdict = "ERROR"
            runtime.ended_at_monotonic = time.monotonic()
        verify = VerifyResult(
            ok=False,
            check_returncode=None,
            check_timed_out=False,
            raw_output_excerpt="",
        )
        worker = WorkerResult(returncode=None, timed_out=False, tokens=None, error=error)
        self._log_attempt(runtime, runtime.task.spec, False, worker, verify, "ERROR", 0)

    async def _run_worker(self, runtime: TaskRuntime, spec: str, attempt: int) -> WorkerResult:
        log_path = runtime.log_path
        engine = self.config.engines.get(runtime.task.engine)
        if engine is None:
            return WorkerResult(
                returncode=None,
                timed_out=False,
                tokens=None,
                error=f"unknown worker engine: {runtime.task.engine}",
            )
        if runtime.task.full_access and not self.config.allow_full_access:
            return WorkerResult(
                returncode=None,
                timed_out=False,
                tokens=None,
                error=(
                    f"task requested full_access with engine {runtime.task.engine}, "
                    "but config allow_full_access is false"
                ),
            )
        cmd = build_worker_command(
            engine,
            taskdir=runtime.taskdir,
            spec=spec,
            full_access=runtime.task.full_access,
            engine_args=runtime.task.engine_args,
            model=runtime.task.model,
        )
        if self.config.steering.dir is not None:
            original_cmd = cmd
            steering_state: dict[str, Any] = {
                "profile": None,
                "version": None,
                "rule_ids": [],
            }
            steering_line = "[ringer.py] steering: no profile matched\n"
            try:
                resolved_model = resolved_task_model(runtime.task, engine, cmd)
                profile = resolve_steering_profile(self.config.steering.dir, resolved_model)
                injected_spec, rule_ids = inject_steering_spec(
                    spec,
                    profile,
                    inject_candidates=self.config.steering.inject_candidates,
                )
                if profile is not None:
                    steering_state = {
                        "profile": profile.slug,
                        "version": profile.profile_version,
                        "rule_ids": list(rule_ids),
                    }
                    shown_rules = ", ".join(rule_ids) if rule_ids else "(none)"
                    steering_line = (
                        f"[ringer.py] steering: profile={profile.slug} "
                        f"version={profile.profile_version} rule_ids={shown_rules}\n"
                    )
                if injected_spec != spec:
                    cmd = build_worker_command(
                        engine,
                        taskdir=runtime.taskdir,
                        spec=injected_spec,
                        full_access=runtime.task.full_access,
                        engine_args=runtime.task.engine_args,
                        model=runtime.task.model,
                    )
            except Exception:
                cmd = original_cmd
                steering_state = {"profile": None, "version": None, "rule_ids": []}
                steering_line = "[ringer.py] steering: no profile matched\n"
            with self.lock:
                runtime.steering = steering_state
            with contextlib.suppress(Exception):
                append_text(log_path, steering_line)
        with self.lock:
            runtime.last_worker_command = list(cmd)
        append_text(
            log_path,
            "\n"
            f"[ringer.py] attempt {attempt} started {datetime.now(timezone.utc).isoformat()}\n"
            f"[ringer.py] engine: {runtime.task.engine}\n"
            f"[ringer.py] command: {shell_command_for_display(cmd)} < /dev/null\n",
        )
        capture = RollingBytes(max_bytes=1_000_000)
        try:
            log_fh = log_path.open("ab")
        except OSError as exc:
            return WorkerResult(returncode=None, timed_out=False, tokens=None, error=str(exc))
        async with AsyncFileCloser(log_fh):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(runtime.taskdir),
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
            except Exception as exc:
                message = f"[ringer.py] worker spawn failed: {exc}\n"
                log_fh.write(message.encode("utf-8", errors="replace"))
                log_fh.flush()
                return WorkerResult(returncode=None, timed_out=False, tokens=None, error=str(exc))
            with self.lock:
                runtime.worker_pid = proc.pid
            self.active_processes[proc.pid] = proc
            reader = asyncio.create_task(self._tee_stream(proc, log_fh, capture))
            timed_out = False
            try:
                await asyncio.wait_for(proc.wait(), timeout=runtime.task.timeout_s)
            except asyncio.TimeoutError:
                timed_out = True
                terminate_process_group(proc)
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    kill_process_group(proc)
                    await proc.wait()
            try:
                await asyncio.wait_for(reader, timeout=5)
            except asyncio.TimeoutError:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reader
            self.active_processes.pop(proc.pid, None)
        output_tail = capture.text()
        tokens = parse_token_count(output_tail, engine.token_regex)
        reported_model = parse_reported_model(output_tail, engine.model_report_regex)
        if timed_out:
            append_text(log_path, f"\n[ringer.py] worker timed out after {runtime.task.timeout_s}s\n")
        append_text(log_path, f"[ringer.py] attempt {attempt} exited rc={proc.returncode}\n")
        return WorkerResult(
            returncode=proc.returncode,
            timed_out=timed_out,
            tokens=tokens,
            reported_model=reported_model,
        )

    async def _tee_stream(
        self,
        proc: asyncio.subprocess.Process,
        log_fh: Any,
        capture: "RollingBytes",
    ) -> None:
        if proc.stdout is None:
            return
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                return
            log_fh.write(chunk)
            log_fh.flush()
            capture.extend(chunk)
            try:
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
            except Exception:
                pass

    def _log_attempt(
        self,
        runtime: TaskRuntime,
        spec: str,
        retrying: bool,
        worker: WorkerResult,
        verify: VerifyResult,
        verdict: str,
        duration_ms: int,
    ) -> None:
        engine = self.config.engines.get(runtime.task.engine)
        resolved_model = resolved_task_model(
            runtime.task,
            engine,
            runtime.last_worker_command,
        )
        reported_model = model_log_text(worker.reported_model) or None
        mismatch = bool(reported_model and resolved_model and reported_model != resolved_model)
        stamped_model = reported_model or resolved_model
        expected_model = resolved_model if mismatch else None
        if mismatch:
            with contextlib.suppress(Exception):
                append_text(
                    runtime.log_path,
                    f"[ringer.py] identity: harness reported {reported_model} "
                    f"but manifest/config expected {resolved_model}\n",
                )
        reasoning_effort = effective_reasoning_effort_from_command(
            runtime.last_worker_command
        )
        notes_parts = [
            f"retry={'true' if retrying else 'false'}",
            f"worker_returncode={worker.returncode}",
            f"model={stamped_model}",
            f"task_type={runtime.task.task_type}",
        ]
        if worker.error:
            notes_parts.append(f"worker_error={worker.error}")
        if verify.missing_files:
            notes_parts.append(f"missing_expect_files={json.dumps(list(verify.missing_files))}")
        notes_parts.append("raw_check_output_first_2000_chars:")
        notes_parts.append(verify.raw_output_excerpt)
        with contextlib.suppress(Exception):
            self._write_steering_observation(
                runtime,
                resolved_model=stamped_model,
                retrying=retrying,
                worker=worker,
                verify=verify,
                verdict=verdict,
                duration_ms=duration_ms,
            )
        self.logger.log_attempt(
            {
                "run_id": self.run_id,
                "pattern": "ringer-py",
                "task_key": runtime.task.key,
                "spec": spec[:500],
                "worker_engine": runtime.task.engine,
                "shepherd_model": SHEPHERD_MODEL,
                "verify_method": VERIFY_METHOD,
                "verdict": verdict,
                "duration_ms": duration_ms,
                "worker_tokens": worker.tokens,
                "notes": "\n".join(notes_parts),
                "orchestrator": self.identity,
                "model": stamped_model,
                "reported_model": reported_model,
                "expected_model": expected_model,
                "reasoning_effort": reasoning_effort,
                "task_type": runtime.task.task_type,
                "retry": retrying,
            }
        )

    def _write_steering_observation(
        self,
        runtime: TaskRuntime,
        *,
        resolved_model: str,
        retrying: bool,
        worker: WorkerResult,
        verify: VerifyResult,
        verdict: str,
        duration_ms: int,
    ) -> None:
        steering_dir = self.config.steering.dir
        if steering_dir is None:
            return
        try:
            steering_state = runtime.steering
            if steering_state is None:
                profile = resolve_steering_profile(steering_dir, resolved_model)
                steering_state = {
                    "profile": profile.slug if profile else None,
                    "version": profile.profile_version if profile else None,
                    "rule_ids": [],
                }
                with self.lock:
                    runtime.steering = steering_state
            now = datetime.now(timezone.utc)
            row = {
                "ts": now.isoformat(),
                "source": "ringer.py",
                "run_id": self.run_id,
                "run_name": self.manifest.run_name,
                "task_key": runtime.task.key,
                "task_type": runtime.task.task_type,
                "engine": runtime.task.engine,
                "model": resolved_model,
                "profile": steering_state.get("profile"),
                "profile_version": steering_state.get("version"),
                "rules_injected": list(steering_state.get("rule_ids", [])),
                "attempt": runtime.attempts,
                "retry": retrying,
                "verdict": verdict,
                "duration_ms": duration_ms,
                "worker_tokens": worker.tokens,
                "check_excerpt": verify.raw_output_excerpt[:500],
            }
            path = (
                steering_dir
                / "observations"
                / "ringer"
                / f"{now.strftime('%Y-%m-%d')}.jsonl"
            )
            append_text(path, json.dumps(row, sort_keys=True) + "\n")
        except Exception as exc:
            with contextlib.suppress(Exception):
                append_text(
                    runtime.log_path,
                    f"[ringer.py] steering: observation write failed {exc}\n",
                )

    def _task_runtime(self, task: TaskSpec) -> TaskRuntime:
        taskdir = self._taskdir(task)
        log_path = self._log_path(task, taskdir)
        with contextlib.suppress(FileNotFoundError):
            log_path.unlink()
        return TaskRuntime(
            task=task,
            taskdir=taskdir,
            log_path=log_path,
            spec_short=shorten(task.spec, 120),
        )

    def _taskdir(self, task: TaskSpec) -> Path:
        taskdir = (self.manifest.workdir / task.key).resolve()
        workdir = self.manifest.workdir.resolve()
        if taskdir != workdir and workdir not in taskdir.parents:
            raise ValueError(f"task key escapes workdir: {task.key}")
        return taskdir

    def _log_path(self, task: TaskSpec, taskdir: Path) -> Path:
        if not self.manifest.worktrees:
            return taskdir / "worker.log"
        logs_dir = (self.manifest.workdir / "logs").resolve()
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = (logs_dir / f"{task.key}.worker.log").resolve()
        if log_path != logs_dir and logs_dir not in log_path.parents:
            raise ValueError(f"task key escapes logs dir: {task.key}")
        return log_path


class RollingBytes:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self.data = bytearray()

    def extend(self, chunk: bytes) -> None:
        self.data.extend(chunk)
        overflow = len(self.data) - self.max_bytes
        if overflow > 0:
            del self.data[:overflow]

    def text(self) -> str:
        return bytes(self.data).decode("utf-8", errors="replace")


class AsyncFileCloser:
    def __init__(self, fh: Any) -> None:
        self.fh = fh

    async def __aenter__(self) -> Any:
        return self.fh

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        self.fh.close()


def verdict_for(worker: WorkerResult, verify: VerifyResult) -> str:
    if worker.error:
        return "ERROR"
    if worker.timed_out or verify.check_timed_out:
        return "TIMEOUT"
    if verify.ok:
        return "PASS"
    return "FAIL"


def build_run_id(run_name: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", run_name.strip()).strip("-")
    # pid suffix: same-second launches of the same run_name must not collide
    # (concurrent ringer runs would otherwise share a state file and eval run_id).
    return f"{safe_name or 'ringer'}-{stamp}-p{os.getpid()}"


def find_repo_identity(start: Path | None = None) -> str | None:
    """Per-repo agent identity: nearest .fleet-agent file walking up from start.

    Jon's fleet convention (2026-07-02): each repo has its own agent name
    (projects.agent_name in the fleet DB); a .fleet-agent file in the repo
    root mirrors it so stdlib-only tools like ringer resolve it without a
    database connection.
    """
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / ".fleet-agent"
        try:
            if candidate.is_file():
                name = re.sub(r"[^A-Za-z0-9_-]", "", candidate.read_text(encoding="utf-8", errors="replace").strip())
                if name:
                    return name
        except OSError:
            continue
    return None


def resolve_identity(
    value: str | None,
    config: AppConfig,
    identity_start_paths: Iterable[Path] = (),
) -> str:
    repo_identities = [find_repo_identity(start) for start in identity_start_paths]
    for candidate in (
        value,
        os.environ.get("FLEET_IDENTITY"),
        os.environ.get(f"{ENV_VAR_PREFIX}_IDENTITY"),
        *repo_identities,
        find_repo_identity(),
        config.identity_default,
    ):
        if candidate and candidate.strip():
            return candidate.strip()
    return socket.gethostname().split(".", 1)[0] or TOOL_NAME


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def parse_token_count(text: str, token_regex: str | None = DEFAULT_TOKEN_REGEX) -> int | None:
    if token_regex:
        matches = list(re.finditer(token_regex, text, flags=re.IGNORECASE))
        for match in reversed(matches):
            groups = [item for item in match.groups() if item]
            value = groups[0] if groups else match.group(0)
            number = re.search(r"([0-9][0-9,]*)", value)
            if number:
                return int(number.group(1).replace(",", ""))
        return None
    matches = re.findall(r"tokens\s+used\s*:?\s*([0-9][0-9,]*)", text, flags=re.IGNORECASE)
    if not matches:
        matches = re.findall(
            r"tokens\s+used\s*\r?\n\s*([0-9][0-9,]*)",
            text,
            flags=re.IGNORECASE,
        )
    if not matches:
        return None
    return int(matches[-1].replace(",", ""))


def parse_reported_model(text: str, model_report_regex: str | None) -> str | None:
    if not model_report_regex:
        return None
    match = re.search(model_report_regex, text, flags=re.IGNORECASE)
    if match is None or match.lastindex is None:
        return None
    value = match.group(1).strip()
    return value or None


def effective_model_from_command(command: list[str]) -> str:
    """Return the model selected by a composed worker argv, if present."""
    for index, item in enumerate(command):
        if item in {"-m", "--model"}:
            if index + 1 >= len(command):
                return ""
            return command[index + 1]
        if item.startswith("--model="):
            return item.removeprefix("--model=")
    return ""


def effective_reasoning_effort_from_command(command: list[str]) -> str | None:
    """Return an explicitly configured model reasoning effort from worker argv."""
    for item in command:
        match = re.search(
            r"(?:^|[=,\s])model_reasoning_effort\s*=\s*[\"']?([^\"',\s]+)",
            item,
        )
        if match:
            effort = match.group(1).strip()
            return effort or None
    return None


def resolved_task_model(
    task: TaskSpec,
    engine: EngineConfig | None,
    command: list[str] | None = None,
) -> str:
    return (
        task.model
        or (engine.model_default if engine else "")
        or effective_model_from_command(command or [])
    )


def build_worker_command(
    engine: EngineConfig,
    *,
    taskdir: Path,
    spec: str,
    full_access: bool,
    engine_args: tuple[str, ...] = (),
    model: str = "",
) -> list[str]:
    access_args = engine.full_access_args if full_access else engine.sandbox_args
    resolved_model = model or engine.model_default
    command = [engine.bin]
    for item in engine.args_template:
        if item == "{access_args}":
            command.extend(access_args)
            continue
        if item == "{model_args}":
            if resolved_model:
                command.extend(("-m", resolved_model))
            continue
        if item == "{engine_args}":
            command.extend(engine_args)
            continue
        if item == "{sandbox_args}":
            command.extend(engine.sandbox_args)
            continue
        if item == "{full_access_args}":
            command.extend(engine.full_access_args)
            continue
        command.append(
            item.replace("{taskdir}", str(taskdir))
            .replace("{spec}", spec)
            .replace("{model}", resolved_model)
        )
    return command


def print_steering_notes(manifest: Manifest, config: AppConfig) -> None:
    """Surface driver-audience guidance once per resolved model. Never raise."""
    try:
        if config.steering.dir is None:
            return
        seen_models: set[str] = set()
        for task in manifest.tasks:
            try:
                engine = config.engines.get(task.engine)
                command: list[str] = []
                if engine is not None:
                    command = build_worker_command(
                        engine,
                        taskdir=(manifest.workdir / task.key).resolve(),
                        spec=task.spec,
                        full_access=task.full_access,
                        engine_args=task.engine_args,
                        model=task.model,
                    )
                model = resolved_task_model(task, engine, command)
                if not model or model in seen_models:
                    continue
                seen_models.add(model)
                profile = resolve_steering_profile(config.steering.dir, model)
                if profile is None:
                    continue
                rules = tuple(
                    rule
                    for rule in profile.rules
                    if rule.audience == "driver"
                    and rule.status in {"confirmed", "candidate", "stale-pending-reverify"}
                )
                if not rules:
                    continue
                print(
                    f"Steering notes for {model} (v{profile.profile_version}) "
                    "— apply when writing specs/feedback:"
                )
                for rule in rules:
                    print(f"- ({rule.status}) {rule.inject}")
            except Exception:
                continue
    except Exception:
        return


ENGINE_INSTALL_HINTS = {
    "codex": "install it with `npm install -g @openai/codex` (or `brew install --cask codex`), then run `codex login`",
    "opencode": "install it with `curl -fsSL https://opencode.ai/install | bash`, then run `opencode auth login`",
}


def preflight_engine_bins(manifest: Manifest, config: AppConfig) -> None:
    """Fail before spawning anything if a worker binary is missing.

    Without this, a fresh install dies mid-run with a bare
    "worker spawn failed: [Errno 2]" in the task log — the least helpful
    possible first experience.
    """
    for name in sorted({task.engine for task in manifest.tasks}):
        engine = config.engines.get(name)
        if engine is None:
            continue  # validate_manifest_engines already rejected this
        bin_path = engine.bin
        if os.sep in bin_path:
            found = Path(bin_path).expanduser()
            missing = not (found.is_file() and os.access(found, os.X_OK))
        else:
            missing = shutil.which(bin_path) is None
        if missing:
            hint = ENGINE_INSTALL_HINTS.get(name, f"install it or fix engines.{name}.bin in config.toml")
            raise ValueError(
                f"engine '{name}' binary not found ({bin_path}) — {hint}"
            )


def validate_manifest_engines(manifest: Manifest, config: AppConfig) -> None:
    missing = sorted({task.engine for task in manifest.tasks if task.engine not in config.engines})
    if missing:
        raise ValueError(f"unknown worker engine(s): {', '.join(missing)}")
    for task in manifest.tasks:
        engine = config.engines[task.engine]
        requires_model = any("{model}" in item for item in engine.args_template)
        accepts_model = requires_model or "{model_args}" in engine.args_template
        if requires_model and not (task.model or engine.model_default):
            raise ValueError(
                f"task {task.key}: engine {engine.name} needs a model — set the task's "
                f"\"model\" field or engines.{engine.name}.model_default in config.toml"
            )
        if task.model and not accepts_model:
            raise ValueError(
                f"task {task.key}: \"model\" is set but engine {engine.name} has no "
                "{model} placeholder in its args_template, so it would be silently ignored"
            )


def read_dashboard_html() -> str:
    try:
        return DASHBOARD_HTML_PATH.read_text(encoding="utf-8")
    except OSError:
        return MINIMAL_DASHBOARD_HTML


def tail_lines(path: Path, line_count: int) -> list[str]:
    if line_count <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 8192))
            data = fh.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    return text.splitlines()[-line_count:]


def tail_file_text(path: Path, max_bytes: int) -> str:
    if max_bytes <= 0 or not path.exists():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
    except OSError:
        return ""
    return data.decode("utf-8", errors="replace")


def tail_text(path: Path, max_bytes: int = 6000, line_count: int = 40) -> str:
    if not path.exists():
        return ""
    try:
        with path.open("rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - max_bytes))
            data = fh.read()
    except OSError:
        return ""
    return "\n".join(data.decode("utf-8", errors="replace").splitlines()[-line_count:])


ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
CMD_JSON_DOUBLE_RE = re.compile(
    r'"(?:cmd|command)"\s*:\s*"(?P<cmd>(?:\\.|[^"\\])*)"',
    re.IGNORECASE,
)
CMD_JSON_SINGLE_RE = re.compile(
    r"'(?:cmd|command)'\s*:\s*'(?P<cmd>(?:\\.|[^'\\])*)'",
    re.IGNORECASE,
)
CMD_LABEL_RE = re.compile(
    r"\b(?:exec(?:_command|/command)?|shell command|command)\b\s*[:=]\s*(?P<cmd>.+)$",
    re.IGNORECASE,
)
CMD_RAN_RE = re.compile(r"^\s*(?:[*>-]\s*)?(?:ran|running)\s+`?(?P<cmd>.+?)`?\s*$", re.IGNORECASE)
CMD_PROMPT_RE = re.compile(r"^\s*(?:\$|\+)\s+(?P<cmd>.+)$")
PATCH_FILE_RE = re.compile(r"^\*\*\*\s+(?:Add|Update)\s+File:\s+(?P<path>.+)$", re.IGNORECASE)
WRITE_QUOTED_FILE_RE = re.compile(
    r"\b(?:created|modified|updated|wrote|writing|saved|edited|patched)\b[^`'\"]{0,48}"
    r"[`'\"](?P<path>[^`'\"]+)[`'\"]",
    re.IGNORECASE,
)
WRITE_FILE_RE = re.compile(
    r"\b(?:created|modified|updated|wrote|writing|saved|edited|patched)\s+"
    r"(?:file\s+)?(?P<path>[A-Za-z0-9_./~:-]+\.[A-Za-z0-9][A-Za-z0-9_+-]*)",
    re.IGNORECASE,
)
ASSISTANT_PREFIX_RE = re.compile(
    r"^\s*(?:assistant|codex(?:-[A-Za-z0-9_-]+)?|agent)\s*(?:>|:|-)\s*(?P<text>.+)$",
    re.IGNORECASE,
)


def worker_activity(path: Path, log_tail: list[str]) -> str:
    text = tail_text(path, max_bytes=ACTIVITY_TAIL_BYTES, line_count=80)
    if text:
        for finder in (last_shell_command_activity, last_written_file_activity, last_assistant_activity):
            activity = finder(text)
            if activity:
                return activity
    return activity_fallback(log_tail)


def last_shell_command_activity(text: str) -> str:
    for line in reversed(non_empty_log_lines(text)):
        command = extract_shell_command(line)
        if command:
            return f"ran: {shorten(command, ACTIVITY_TEXT_LIMIT)}"
    return ""


def last_written_file_activity(text: str) -> str:
    for line in reversed(non_empty_log_lines(text)):
        path = extract_written_file(line)
        if path:
            return f"wrote {shorten(path, ACTIVITY_TEXT_LIMIT)}"
    return ""


def last_assistant_activity(text: str) -> str:
    lines = list(reversed(non_empty_log_lines(text)))
    for line in lines:
        match = ASSISTANT_PREFIX_RE.match(line)
        if match:
            candidate = clean_log_text(match.group("text"))
            if candidate:
                return shorten(candidate, ACTIVITY_TEXT_LIMIT)
    for line in lines:
        if looks_like_assistant_text(line):
            return shorten(clean_log_text(line), ACTIVITY_TEXT_LIMIT)
    return ""


def activity_fallback(log_tail: list[str]) -> str:
    for line in reversed(log_tail):
        candidate = clean_log_text(line)
        if candidate:
            return shorten(candidate, ACTIVITY_TEXT_LIMIT)
    return ""


def non_empty_log_lines(text: str) -> list[str]:
    return [line for line in (clean_log_text(raw) for raw in text.splitlines()) if line]


def clean_log_text(value: str) -> str:
    value = ANSI_RE.sub("", value)
    return " ".join(value.strip().split())


def extract_shell_command(line: str) -> str:
    if line.startswith("[ringer.py]"):
        return ""
    for pattern in (CMD_JSON_DOUBLE_RE, CMD_JSON_SINGLE_RE, CMD_LABEL_RE, CMD_RAN_RE, CMD_PROMPT_RE):
        match = pattern.search(line)
        if not match:
            continue
        command = clean_command(match.group("cmd"))
        if command and looks_like_shell_command(command):
            return command
    return ""


def clean_command(value: str) -> str:
    command = value.strip().strip("`")
    if len(command) >= 2 and command[0] == command[-1] and command[0] in {"'", '"'}:
        command = command[1:-1]
    command = command.replace(r"\n", " ").replace(r"\t", " ")
    command = command.replace(r"\"", '"').replace(r"\'", "'")
    command = re.split(r"\s+<\s*/dev/null\b", command, maxsplit=1)[0]
    return clean_log_text(command).strip(" ,")


def looks_like_shell_command(command: str) -> bool:
    if not command or command.startswith(("{", "[", "(", "<")):
        return False
    lower = command.lower()
    if lower.startswith(("error ", "unknown ", "none ", "failed ")):
        return False
    if lower.startswith("codex exec "):
        return False
    try:
        first = shlex.split(command)[0]
    except ValueError:
        first = command.split(maxsplit=1)[0]
    return bool(re.match(r"^(?:[A-Za-z0-9_./-]+)(?:\.[A-Za-z0-9_+-]+)?$", first))


def extract_written_file(line: str) -> str:
    if line.startswith("[ringer.py]"):
        return ""
    for pattern in (PATCH_FILE_RE, WRITE_QUOTED_FILE_RE, WRITE_FILE_RE):
        match = pattern.search(line)
        if not match:
            continue
        path = normalize_activity_path(match.group("path"))
        if path:
            return path
    return ""


def normalize_activity_path(value: str) -> str:
    path = value.strip().strip("`'\".,;:)")
    path = re.sub(r":\d+(?::\d+)?$", "", path)
    if not re.search(r"\.[A-Za-z0-9][A-Za-z0-9_+-]*(?:$|[?#])", path):
        return ""
    if path.startswith("/"):
        return Path(path).name
    return path


def looks_like_assistant_text(line: str) -> bool:
    lower = line.lower()
    if not line or line.startswith(("[", "{", "}", "```", "***", "@@", "$", "+")):
        return False
    if re.match(r"^(?:exec|command|stdout|stderr|chunk id|wall time|process exited)\b", lower):
        return False
    if re.match(r"^(?:error|warning|info|debug)[:\s]", lower):
        return False
    if re.match(r"^[A-Za-z_][A-Za-z0-9_./-]*:\d+(?::\d+)?:", line):
        return False
    return bool(re.search(r"[A-Za-z]", line))


def build_failure_context(log_path: Path, raw_check_output: str) -> str:
    worker_tail = tail_text(log_path)
    context = f"{worker_tail}\n{raw_check_output}".strip()
    if len(context) > 6000:
        return context[-6000:]
    return context


def shorten(value: str, limit: int) -> str:
    clean = " ".join(value.split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 3)] + "..."


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text)


def terminate_process_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass


def kill_process_group(proc: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def shell_command_for_display(parts: Iterable[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def dry_run(
    manifest: Manifest,
    config: AppConfig,
    identity: str,
    dashboard_enabled: bool,
    force_browser: bool,
) -> None:
    print("DRY RUN: no codex workers will be spawned.")
    print(f"Run: {manifest.run_name}")
    print(f"Identity: {identity}")
    print(f"Config: {config.path if config.path else '(safe defaults)'}")
    print(f"Workdir: {manifest.workdir}")
    print(f"Max parallel: {manifest.max_parallel}")
    print(f"Worktrees: {manifest.worktrees} repo={manifest.repo}")
    print(f"State dir: {config.state_dir}")
    print(f"Eval backend: {config.eval.backend}")
    print(f"Dashboard: {'on' if dashboard_enabled else 'off'}")
    if dashboard_enabled:
        mode = "browser"
        if not force_browser and config.hud_app_path is not None:
            mode = f"HUD app {config.hud_app_path} when available, browser fallback"
        print(f"Dashboard opener: {mode}")
        print(f"Dashboard port base: {config.dashboard_port_base}")
    print(f"Artifacts: {'on' if config.artifact.enabled else 'off'}")
    if config.artifact.enabled:
        run_id_preview = build_run_id(manifest.run_name)
        print(f"  live status page: {config.artifact.artifact_path(run_id_preview, manifest.run_name)}")
        print(f"  final report:     {config.artifact.report_path(run_id_preview, manifest.run_name)}")
        print(f"  runs index:       {config.artifact.index_out}")
    print("Tasks:")
    for task in manifest.tasks:
        taskdir = (manifest.workdir / task.key).resolve()
        engine = config.engines.get(task.engine)
        full_access_allowed = task.full_access and config.allow_full_access
        cmd = (
            build_worker_command(
                engine,
                taskdir=taskdir,
                spec=task.spec,
                full_access=task.full_access,
                engine_args=task.engine_args,
                model=task.model,
            )
            if engine is not None
            else []
        )
        print(f"  - {task.key}")
        print(f"    engine: {task.engine}")
        print(f"    dir: {taskdir}")
        print(f"    timeout_s: {task.timeout_s}")
        if task.full_access:
            print(f"    full_access: true allowed={full_access_allowed}")
        else:
            print("    full_access: false")
        print(f"    expect_files: {list(task.expect_files)}")
        print(f"    check: {task.check}")
        if engine is None:
            print("    command: ERROR unknown engine")
        elif task.full_access and not config.allow_full_access:
            print("    command: ERROR full_access requires allow_full_access=true in config")
        else:
            print(f"    command: {shell_command_for_display(cmd)} < /dev/null")


def print_lint_findings(findings: list[str]) -> None:
    for finding in findings:
        print(f"lint: {finding}")


def print_summary(run_id: str, runtimes: list[TaskRuntime]) -> None:
    print("\nSummary")
    print(f"run_id: {run_id}")
    header = f"{'task':<24} {'status':<8} {'verdict':<8} {'attempts':>8} {'tokens':>10} {'elapsed_s':>10}"
    print(header)
    print("-" * len(header))
    now = time.monotonic()
    for runtime in runtimes:
        tokens = "" if runtime.tokens is None else str(runtime.tokens)
        print(
            f"{runtime.task.key:<24} {runtime.status:<8} "
            f"{(runtime.final_verdict or ''):<8} {runtime.attempts:>8} "
            f"{tokens:>10} {runtime.elapsed_s(now):>10.1f}"
        )


def create_demo_manifest() -> Path:
    root = Path(tempfile.mkdtemp(prefix="ringer-demo-"))
    workdir = root / "work"
    manifest = {
        "run_name": "ringer-demo",
        "workdir": str(workdir),
        "max_parallel": 3,
        "worktrees": False,
        "repo": None,
        "tasks": [
            {
                "key": "alpha",
                "spec": "Create alpha.txt in the current working directory containing exactly: alpha ready. Do not write any other files.",
                "check": "test \"$(cat alpha.txt 2>/dev/null)\" = \"alpha ready\" || { echo 'FAIL: alpha.txt missing or content is not alpha ready'; exit 1; }",
                "verified": "alpha.txt exists and contains exactly the expected text",
                "expect_files": ["alpha.txt"],
            },
            {
                "key": "bravo",
                "spec": "Create bravo.txt in the current working directory containing exactly: bravo ready. Do not write any other files.",
                "check": "test \"$(cat bravo.txt 2>/dev/null)\" = \"bravo ready\" || { echo 'FAIL: bravo.txt missing or content is not bravo ready'; exit 1; }",
                "verified": "bravo.txt exists and contains exactly the expected text",
                "expect_files": ["bravo.txt"],
            },
            {
                "key": "charlie",
                "spec": "Create charlie.txt in the current working directory containing exactly: charlie ready. Do not write any other files.",
                "check": "test \"$(cat charlie.txt 2>/dev/null)\" = \"charlie ready\" || { echo 'FAIL: charlie.txt missing or content is not charlie ready'; exit 1; }",
                "verified": "charlie.txt exists and contains exactly the expected text",
                "expect_files": ["charlie.txt"],
            },
        ],
    }
    path = root / "ringer.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return path


def repo_root() -> Path:
    return Path(__file__).resolve().parent


def claude_root(project: bool) -> Path:
    return (Path.cwd() if project else Path.home()) / ".claude"


def ringer_skill_source() -> Path:
    return repo_root() / ".claude" / "skills" / "ringer" / "SKILL.md"


def ringer_hook_command(action: str) -> str:
    hook_path = repo_root() / "hooks" / "ringer_nudge.py"
    return f"python3 {shlex.quote(str(hook_path))} {action}"


def backup_file(path: Path) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = path.with_name(f"{path.name}.bak-{stamp}")
    shutil.copy2(path, backup)
    return backup


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"settings file is not valid JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"settings file must contain a JSON object: {path}")
    return data


def write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_file(path)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(settings, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def hook_command_contains(value: Any, needle: str = "ringer_nudge.py") -> bool:
    return isinstance(value, dict) and needle in str(value.get("command", ""))


def event_has_ringer_hook(groups: Any) -> bool:
    if not isinstance(groups, list):
        return False
    for group in groups:
        if not isinstance(group, dict):
            continue
        handlers = group.get("hooks")
        if isinstance(handlers, list) and any(hook_command_contains(handler) for handler in handlers):
            return True
    return False


def merge_ringer_hook(settings: dict[str, Any], event: str, matcher: str, command: str) -> bool:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings hooks field must be a JSON object")
    groups = hooks.setdefault(event, [])
    if not isinstance(groups, list):
        raise ValueError(f"settings hooks.{event} field must be a JSON array")
    if event_has_ringer_hook(groups):
        return False
    groups.append(
        {
            "matcher": matcher,
            "hooks": [
                {
                    "type": "command",
                    "command": command,
                }
            ],
        }
    )
    return True


def remove_ringer_hooks(settings: dict[str, Any]) -> int:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return 0
    removed = 0
    for event in list(hooks):
        groups = hooks[event]
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            if not isinstance(group, dict):
                kept_groups.append(group)
                continue
            handlers = group.get("hooks")
            if not isinstance(handlers, list):
                kept_groups.append(group)
                continue
            kept_handlers = []
            for handler in handlers:
                if hook_command_contains(handler):
                    removed += 1
                else:
                    kept_handlers.append(handler)
            if kept_handlers:
                new_group = dict(group)
                new_group["hooks"] = kept_handlers
                kept_groups.append(new_group)
        if kept_groups:
            hooks[event] = kept_groups
        else:
            del hooks[event]
    if not hooks:
        del settings["hooks"]
    return removed


def install_agent(project: bool = False) -> int:
    root = claude_root(project)
    skill_source = ringer_skill_source()
    skill_target = root / "skills" / "ringer" / "SKILL.md"
    if not skill_source.exists():
        raise ValueError(f"ringer skill source not found: {skill_source}")
    skill_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(skill_source, skill_target)

    settings_path = root / "settings.json"
    settings = load_settings(settings_path)
    changed = False
    changed |= merge_ringer_hook(
        settings,
        "PreToolUse",
        "Bash",
        ringer_hook_command("pre-bash"),
    )
    changed |= merge_ringer_hook(
        settings,
        "PostToolUse",
        "Edit|Write",
        ringer_hook_command("post-edit"),
    )
    if changed or not settings_path.exists():
        write_settings(settings_path, settings)

    scope = "project" if project else "user"
    print(f"Installed ringer agent for {scope} scope.")
    print(f"Skill: {skill_target}")
    if changed:
        print(f"Hooks: added PreToolUse Bash and PostToolUse Edit|Write in {settings_path}")
    else:
        print(f"Hooks: already present in {settings_path}")
    return 0


def uninstall_agent(project: bool = False) -> int:
    root = claude_root(project)
    settings_path = root / "settings.json"
    removed_hooks = 0
    if settings_path.exists():
        settings = load_settings(settings_path)
        removed_hooks = remove_ringer_hooks(settings)
        if removed_hooks:
            write_settings(settings_path, settings)

    skill_dir = root / "skills" / "ringer"
    removed_skill = False
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
        removed_skill = True

    scope = "project" if project else "user"
    print(f"Uninstalled ringer agent for {scope} scope.")
    print(f"Hooks removed: {removed_hooks}")
    print(f"Skill removed: {'yes' if removed_skill else 'no'}")
    return 0


async def run_manifest(
    manifest: Manifest,
    config: AppConfig,
    identity: str,
    dashboard_enabled: bool,
    force_browser: bool,
) -> int:
    runner = RingerRunner(
        manifest,
        config=config,
        identity=identity,
        dashboard_enabled=dashboard_enabled,
        force_browser=force_browser,
    )
    register_active_run(
        runner.run_id,
        identity,
        manifest.run_name,
        manifest.workdir,
        started_at=runner.started_at,
    )
    try:
        return await runner.run()
    finally:
        unregister_active_run(runner.run_id)


def hud_is_alive(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs", timeout=0.4) as response:
            return response.status == 200
    except Exception:
        return False


def open_in_browser(url: str) -> None:
    # `open` is the reliable path on macOS; webbrowser can silently no-op
    # depending on how the session was launched (observed during demo prep).
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            webbrowser.open(url)
    except Exception:
        pass


def ensure_hud_running(config: AppConfig, *, open_browser: bool) -> None:
    """Make sure the persistent Ringside page is up before a run starts.

    The human should never have to remember a second command to watch the
    fight: if no hud answers on the configured port, spawn one detached.
    """
    port = config.hud_port
    url = f"http://127.0.0.1:{port}"
    already_alive = hud_is_alive(port)
    if not already_alive:
        log_path = config.state_dir / "hud.log"
        with contextlib.suppress(Exception):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("ab") as log_file:
                subprocess.Popen(
                    [sys.executable, str(Path(__file__).resolve()), "hud", "--no-open", "--port", str(port)],
                    stdout=log_file,
                    stderr=log_file,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,
                )
        for _ in range(20):
            if hud_is_alive(port):
                break
            time.sleep(0.15)
    if open_browser and not already_alive and hud_is_alive(port):
        open_in_browser(url)
    print(f"Ringside: {url}", flush=True)


def run_persistent_hud(config: AppConfig, *, port: int | None, open_viewer: bool) -> int:
    chosen_port = port if port is not None else config.hud_port
    if hud_is_alive(chosen_port):
        url = f"http://127.0.0.1:{chosen_port}"
        print(f"Ringside is already running: {url}")
        if open_viewer:
            open_in_browser(url)
        return 0
    server = PersistentHudServer(
        config.state_dir,
        preferred_port=chosen_port,
        open_viewer=open_viewer,
    )
    server.model_log_path = config.eval.jsonl_path
    server.default_model_log_path = config.eval.jsonl_path
    server.start()
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        print("\nRingside stopped.")
        return 0
    finally:
        server.stop()



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ringer.py",
        description=(
            "Ringer: deterministic parallel AI-agent orchestrator. Runs manifest tasks in parallel, "
            "verifies artifacts with executed checks, retries failures once, logs eval rows, "
            "and serves a live dashboard."
        ),
    )
    parser.add_argument("--config", type=Path, help="path to config.toml (default: XDG config path)")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="run a ringer manifest")
    run_parser.add_argument("manifest", type=Path, help="path to ringer.json")
    run_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    run_parser.add_argument("--max-parallel", type=int, help="override manifest max_parallel")
    run_parser.add_argument("--identity", help="orchestrator identity for HUD state and eval rows")
    run_parser.add_argument("--no-dashboard", action="store_true", help="disable live dashboard")
    run_parser.add_argument("--browser", action="store_true", help="open the dashboard in the browser instead of Ringside")
    run_parser.epilog = "Set RINGER_NO_CATALOG_REFRESH=1 to skip the non-blocking OpenRouter catalog auto-refresh."
    run_parser.add_argument(
        "--no-artifact",
        action="store_true",
        help="disable zero-LLM HTML status/report artifacts (see [artifact] in config.toml)",
    )
    run_parser.add_argument("--dry-run", action="store_true", help="print the plan without spawning codex")

    lint_parser = subparsers.add_parser("lint", help="lint a ringer manifest")
    lint_parser.add_argument("manifest", type=Path, help="path to ringer.json")

    hud_parser = subparsers.add_parser("hud", help="start the persistent Ringside page in your browser")
    hud_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    hud_parser.add_argument("--port", type=int, help=f"port to bind on 127.0.0.1 (default: {DEFAULT_HUD_PORT})")
    hud_parser.add_argument("--no-open", action="store_true", help="start the server without opening a browser")

    db_parser = subparsers.add_parser("db", help="manage the derived SQLite read model")
    db_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    db_subparsers = db_parser.add_subparsers(dest="db_command", required=True)
    for name in ("rebuild", "sync"):
        sub = db_subparsers.add_parser(name, help=f"{name} the derived SQLite read model")
        sub.add_argument("--db", type=Path, help="path to SQLite read model (default: ~/.ringer/ringer.db)")
        sub.add_argument("--log", type=Path, help="path to local eval JSONL log")
        sub.add_argument("--catalog-file", type=Path, help="path to local OpenRouter catalog snapshot")
        sub.add_argument("--registry", type=Path, help="path to model identity registry")

    models_parser = subparsers.add_parser("models", help="show the local per-model performance scoreboard")
    models_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    models_parser.add_argument("--log", type=Path, help="path to local eval JSONL log")
    models_parser.add_argument("--db", type=Path, help="path to SQLite read model (default: ~/.ringer/ringer.db)")
    models_parser.add_argument("--task-type", help="only include one task_type bucket")
    models_parser.add_argument("--model", help="only include one resolved model bucket")
    models_parser.add_argument("--engine", help="only include rows from one worker engine")
    models_parser.add_argument("--since", help="only include rows logged on or after YYYY-MM-DD")
    models_parser.add_argument("--explore", action="store_true", help="show proven/probation tiers plus cheap untested catalog candidates")
    models_parser.add_argument("--catalog-file", type=Path, help="path to local OpenRouter catalog snapshot")
    models_parser.add_argument("--notes-file", type=Path, default=default_model_notes_path(), help="path to MODEL-NOTES.md judgment layer")
    models_parser.add_argument("--registry", type=Path, default=default_model_registry_path(), help=argparse.SUPPRESS)
    models_parser.add_argument("--html", nargs="?", const="", help="render a self-contained HTML scoreboard; optional output path")
    models_parser.add_argument("--open", action="store_true", help="render the HTML scoreboard to the artifact library and open it")
    models_parser.add_argument("--json", action="store_true", help="print the scoreboard as JSON")

    catalog_parser = subparsers.add_parser("catalog", help="show or refresh the local OpenRouter model catalog")
    catalog_parser.add_argument("--refresh", action="store_true", help="fetch source and rewrite the local snapshot")
    catalog_parser.add_argument("--source", help=f"OpenRouter models URL or fixture file (default: {DEFAULT_CATALOG_SOURCE})")
    catalog_parser.add_argument("--file", type=Path, help="catalog snapshot path (default: ~/.ringer/openrouter-catalog.json)")
    catalog_parser.add_argument("--free", action="store_true", help="show free models only")
    catalog_parser.add_argument("--changes", action="store_true", help="show recent catalog changes newest first")
    catalog_parser.add_argument("--json", action="store_true", help="print the model list as JSON and nothing else")

    demo_parser = subparsers.add_parser("demo", help="generate and run a 3-task toy manifest in /tmp")
    demo_parser.add_argument("--config", type=Path, default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    demo_parser.add_argument("--max-parallel", type=int, help="override demo max_parallel")
    demo_parser.add_argument("--identity", help="orchestrator identity for HUD state and eval rows")
    demo_parser.add_argument("--no-dashboard", action="store_true", help="disable live dashboard")
    demo_parser.add_argument("--browser", action="store_true", help="open the dashboard in the browser instead of Ringside")
    demo_parser.add_argument(
        "--no-artifact",
        action="store_true",
        help="disable zero-LLM HTML status/report artifacts (see [artifact] in config.toml)",
    )
    demo_parser.add_argument("--dry-run", action="store_true", help="print the demo plan without spawning codex")

    install_parser = subparsers.add_parser("install-agent", help="install the ringer Claude Code skill and hooks")
    install_parser.add_argument("--project", action="store_true", help="install into ./.claude instead of ~/.claude")

    uninstall_parser = subparsers.add_parser("uninstall-agent", help="remove the ringer Claude Code skill and hooks")
    uninstall_parser.add_argument("--project", action="store_true", help="remove from ./.claude instead of ~/.claude")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Keep progress lines live when stdout is a pipe (tee, orchestrators).
    with contextlib.suppress(Exception):
        sys.stdout.reconfigure(line_buffering=True)
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install-agent":
            return install_agent(project=args.project)
        if args.command == "uninstall-agent":
            return uninstall_agent(project=args.project)

        if args.command == "lint":
            manifest = Manifest.from_path(args.manifest)
            findings = lint_manifest(manifest)
            if findings:
                print_lint_findings(findings)
                return 1
            print(f"lint: clean ({len(manifest.tasks)} tasks)")
            return 0

        if args.command == "catalog":
            return run_catalog_command(args)

        config = AppConfig.load(args.config)
        if args.command == "db":
            return run_db_command(config, args)
        if args.command == "models":
            return run_models_command(config, args)
        if args.command == "hud":
            return run_persistent_hud(
                config,
                port=args.port,
                open_viewer=not args.no_open,
            )

        if args.command == "demo":
            manifest_path = create_demo_manifest()
            print(f"Demo manifest: {manifest_path}")
        else:
            manifest_path = args.manifest
        manifest = Manifest.from_path(manifest_path).with_max_parallel(args.max_parallel)
        with contextlib.suppress(Exception):
            print_steering_notes(manifest, config)
        print_lint_findings(lint_manifest(manifest, include_model_log_nudges=True))
        validate_manifest_engines(manifest, config)
        identity_start_paths = [manifest.workdir]
        if manifest.source_path is not None:
            identity_start_paths.append(manifest.source_path.parent)
        identity = resolve_identity(args.identity, config, identity_start_paths)
        dashboard_enabled = not args.no_dashboard
        if getattr(args, "no_artifact", False) and config.artifact.enabled:
            config = dataclass_replace(config, artifact=dataclass_replace(config.artifact, enabled=False))
        if args.dry_run:
            dry_run(
                manifest,
                config=config,
                identity=identity,
                dashboard_enabled=dashboard_enabled,
                force_browser=args.browser,
            )
            return 0
        preflight_engine_bins(manifest, config)
        if args.command == "run":
            start_catalog_auto_refresh()
        if dashboard_enabled and not args.browser:
            ensure_hud_running(config, open_browser=True)
        return asyncio.run(
            run_manifest(
                manifest,
                config=config,
                identity=identity,
                dashboard_enabled=dashboard_enabled,
                force_browser=args.browser,
            )
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ringer.py: error: {exc}", file=sys.stderr)
        return 2



if __name__ == "__main__":
    raise SystemExit(main())
