#!/usr/bin/env python3
"""Validate an Amazon-style PR/FAQ against a JSON evidence packet."""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from pathlib import Path
from typing import Any


SUCCESSFUL_CHECK = "SUCCESS"
REVIEW_HANDOFF_MODE = "review_handoff"
RETROSPECTIVE_MODE = "retrospective"
LIFECYCLE_FACTS = ("built", "merged", "deployed", "available")


def normalize(value: str) -> str:
    value = re.sub(r"(?<=\d),(?=\d)", "", value.lower())
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def package_mode(evidence: dict[str, Any]) -> str:
    """Return the explicit mode, defaulting safely to the readiness gate."""

    mode = str(evidence.get("mode") or "").strip().lower()
    if mode == RETROSPECTIVE_MODE:
        return RETROSPECTIVE_MODE
    return REVIEW_HANDOFF_MODE


def display_fact(value: str, limit: int = 140) -> str:
    compact = re.sub(r"\s+", " ", value).strip()
    return compact if len(compact) <= limit else f"{compact[: limit - 3]}..."


def heading_labels(text: str) -> list[str]:
    labels: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        is_heading = line.startswith("#") or (
            line.startswith("**") and line.endswith("**")
        )
        if is_heading:
            line = re.sub(r"^#{1,6}\s*", "", line)
            line = re.sub(r"^\*\*|\*\*$", "", line)
            labels.append(normalize(line))
    return labels


def has_labeled_section(labels: list[str], aliases: tuple[str, ...]) -> bool:
    normalized_aliases = tuple(normalize(alias) for alias in aliases)
    return any(
        any(alias in label for alias in normalized_aliases)
        for label in labels
    )


def find_section_tail(text: str, aliases: tuple[str, ...]) -> str:
    normalized_aliases = tuple(normalize(alias) for alias in aliases)
    lines = text.splitlines()
    for index, raw_line in enumerate(lines):
        if not raw_line.lstrip().startswith("#"):
            continue
        label = normalize(re.sub(r"^\s*#{1,6}\s*", "", raw_line))
        if any(alias in label for alias in normalized_aliases):
            return "\n".join(lines[index + 1 :])
    return ""


def verification_signatures(verification: Any) -> list[str]:
    signatures: list[str] = []
    if isinstance(verification, str):
        if verification.strip():
            signatures.append(verification)
    elif isinstance(verification, dict):
        if "name" in verification and "conclusion" in verification:
            signature = f"{verification['name']} {verification['conclusion']}"
            sha = evidence_sha(verification)
            if sha:
                signature = f"{signature} {sha}"
            signatures.append(signature)
        else:
            for value in verification.values():
                signatures.extend(verification_signatures(value))
    elif isinstance(verification, list):
        for value in verification:
            signatures.extend(verification_signatures(value))
    return signatures


def evidence_sha(record: dict[str, Any]) -> str:
    for key in ("sha", "commit_sha", "head_sha", "headRefOid"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evidence_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def successful_conclusion(value: Any) -> bool:
    return str(value).strip().upper() in {SUCCESSFUL_CHECK, "PASS", "PASSED"}


def clean_review_conclusion(value: Any) -> bool:
    normalized = normalize(str(value))
    if "no actionable findings" in normalized or "no findings" in normalized:
        return True
    if any(
        marker in normalized
        for marker in (
            "actionable",
            "not clean",
            "not approved",
            "changes requested",
            "failed",
            "rejected",
        )
    ):
        return False
    return normalized in {"clean", "approved", "passed"}


def authorization_is_recorded(
    authorization: Any,
    aliases: tuple[str, ...],
) -> bool:
    if not isinstance(authorization, dict):
        return False
    return any(authorization.get(alias) is True for alias in aliases)


def add_gate_record_blockers(
    blockers: list[str],
    label: str,
    value: Any,
    exact_sha: str,
) -> None:
    records = evidence_records(value)
    if not records:
        blockers.append(f"{label} evidence is missing")
        return
    if isinstance(value, list) and len(records) != len(value):
        blockers.append(f"{label} evidence contains a non-record entry")
    for index, record in enumerate(records, start=1):
        name = str(record.get("name") or f"record {index}")
        conclusion = record.get("conclusion")
        if not successful_conclusion(conclusion):
            blockers.append(
                f"{label} {name!r} conclusion is "
                f"{conclusion if conclusion is not None else '<missing>'}, not SUCCESS"
            )
        sha = evidence_sha(record)
        if not sha:
            blockers.append(f"{label} {name!r} does not name a commit SHA")
        elif exact_sha and sha.lower() != exact_sha.lower():
            blockers.append(
                f"{label} {name!r} covers SHA {sha}, not exact SHA {exact_sha}"
            )


def readiness_blockers(evidence: dict[str, Any]) -> tuple[list[str], str]:
    """Return every blocker and the exact SHA when branch heads agree."""

    blockers: list[str] = []
    commit = evidence.get("commit")
    if not isinstance(commit, dict):
        commit = {}
    pr = evidence.get("pr")
    if not isinstance(pr, dict):
        pr = {}

    local_head = str(commit.get("local_head") or "").strip()
    remote_head = str(
        commit.get("pushed_remote_branch_head")
        or commit.get("remote_branch_head")
        or commit.get("remote_head")
        or ""
    ).strip()
    pr_head = str(pr.get("headRefOid") or pr.get("head_ref_oid") or "").strip()

    if not local_head:
        blockers.append("local HEAD SHA is missing")
    if not remote_head:
        blockers.append("pushed remote branch head SHA is missing")
    if not pr_head:
        blockers.append("GitHub PR headRefOid is missing")

    exact_sha = ""
    supplied_heads = [value.lower() for value in (local_head, remote_head, pr_head) if value]
    if len(supplied_heads) == 3:
        if len(set(supplied_heads)) == 1:
            exact_sha = local_head
        else:
            blockers.append(
                "local HEAD, pushed remote branch head, and GitHub PR headRefOid "
                f"are not the same SHA (local={local_head}, remote={remote_head}, PR={pr_head})"
            )

    authorization = evidence.get("authorization")
    if not authorization_is_recorded(
        authorization,
        ("pushed_branch", "push", "remote_branch"),
    ):
        blockers.append("authorization for the existing pushed branch is not recorded")
    if not authorization_is_recorded(
        authorization,
        ("open_pr", "pr", "pr_creation_or_update"),
    ):
        blockers.append("authorization for the existing open PR is not recorded")

    if pr.get("number") is None:
        blockers.append("GitHub PR number is missing")
    state = str(pr.get("state") or "<missing>").upper()
    if state != "OPEN":
        blockers.append(f"GitHub PR state is {state}, not OPEN")
    if pr.get("isDraft") is not False:
        blockers.append("GitHub PR is a draft or draft state is missing")

    mergeable = pr.get("mergeable")
    mergeable_value = str(mergeable).upper()
    if mergeable is not True and mergeable_value not in {"MERGEABLE", "CLEAN"}:
        blockers.append(
            f"GitHub mergeability is {mergeable if mergeable is not None else '<missing>'}, "
            "not MERGEABLE"
        )

    verification = evidence.get("verification")
    if not isinstance(verification, dict):
        verification = {}
    add_gate_record_blockers(
        blockers,
        "worker gate",
        verification.get("worker_gates"),
        exact_sha,
    )
    add_gate_record_blockers(
        blockers,
        "full-repository gate",
        verification.get("full_repository_gates"),
        exact_sha,
    )

    review = verification.get("independent_review")
    if not isinstance(review, dict):
        blockers.append("fresh independent review evidence is missing")
    else:
        if review.get("fresh") is not True:
            blockers.append("independent review is not marked fresh")
        if not clean_review_conclusion(review.get("conclusion")):
            blockers.append("independent review has actionable or missing findings")
        review_sha = evidence_sha(review)
        if not review_sha:
            blockers.append("independent review does not name a commit SHA")
        elif exact_sha and review_sha.lower() != exact_sha.lower():
            blockers.append(
                f"independent review covers SHA {review_sha}, not exact SHA {exact_sha}"
            )

    checks_value = verification.get("required_checks")
    if checks_value is None:
        checks_value = verification.get("github_checks")
    checks = evidence_records(checks_value)
    if not checks:
        blockers.append("required GitHub check evidence is missing")
    else:
        if isinstance(checks_value, list) and len(checks) != len(checks_value):
            blockers.append("required GitHub check evidence contains a non-record entry")
        for index, check in enumerate(checks, start=1):
            name = str(check.get("name") or f"record {index}")
            conclusion = check.get("conclusion")
            if str(conclusion).upper() != SUCCESSFUL_CHECK:
                blockers.append(
                    f"required GitHub check {name!r} conclusion is "
                    f"{conclusion if conclusion is not None else '<missing>'}, not SUCCESS"
                )
            check_sha = evidence_sha(check)
            if not check_sha:
                blockers.append(
                    f"required GitHub check {name!r} does not name a commit SHA"
                )
            elif exact_sha and check_sha.lower() != exact_sha.lower():
                blockers.append(
                    f"required GitHub check {name!r} covers SHA {check_sha}, "
                    f"not exact SHA {exact_sha}"
                )

    return blockers, exact_sha


def review_gate_outcome(text: str) -> str:
    outcomes: set[str] = set()
    for raw_line in text.splitlines():
        line = normalize(raw_line)
        if "review gate" not in line and "readiness gate" not in line:
            continue
        if re.search(r"\bready\b", line):
            outcomes.add("READY")
        if re.search(r"\bblocked\b", line):
            outcomes.add("BLOCKED")
    if len(outcomes) == 1:
        return outcomes.pop()
    return ""


def lifecycle_outcome(text: str) -> str:
    outcomes: set[str] = set()
    for raw_line in text.splitlines():
        line = normalize(raw_line)
        if "lifecycle outcome" not in line:
            continue
        if re.search(r"\bretrospective\b", line):
            outcomes.add("RETROSPECTIVE")
        if re.search(r"\bready\b", line):
            outcomes.add("READY")
        if re.search(r"\bblocked\b", line):
            outcomes.add("BLOCKED")
    if len(outcomes) == 1:
        return outcomes.pop()
    return ""


def document_states_boolean_fact(text: str, label: str, value: bool) -> bool:
    expected = "true" if value else "false"
    pattern = re.compile(rf"\b{re.escape(label)}\s+(?:is\s+|was\s+)?{expected}\b")
    return any(pattern.search(normalize(line)) for line in text.splitlines())


def document_states_pr_state(text: str, state: str) -> bool:
    pattern = re.compile(rf"\bpr\s+state\s+(?:is\s+)?{re.escape(state.lower())}\b")
    return bool(pattern.search(normalize(text)))


def has_positive_ready_claim(text: str) -> bool:
    ready_pattern = re.compile(
        r"\b(?:ready\s+(?:for\s+(?:human\s+)?review|to\s+merge|for\s+merge)"
        r"|merge\s+ready)\b",
        re.IGNORECASE,
    )
    negative_pattern = re.compile(
        r"\b(?:not|is\s+not|isn['’]?t|cannot|can['’]?t|do\s+not\s+call)\b",
        re.IGNORECASE,
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if ready_pattern.search(sentence) and not negative_pattern.search(sentence):
            return True
    return False


def deployment_is_false(evidence: dict[str, Any]) -> bool:
    if evidence.get("deployed") is False:
        return True
    lifecycle = evidence.get("lifecycle")
    if isinstance(lifecycle, dict) and lifecycle.get("deployed") is False:
        return True
    safety = evidence.get("safety")
    return isinstance(safety, dict) and safety.get("deployed") is False


def availability_is_false(evidence: dict[str, Any]) -> bool:
    if evidence.get("available") is False:
        return True
    lifecycle = evidence.get("lifecycle")
    if isinstance(lifecycle, dict) and lifecycle.get("available") is False:
        return True
    safety = evidence.get("safety")
    return isinstance(safety, dict) and safety.get("available") is False


def deployment_overclaims(text: str) -> list[str]:
    claims: list[str] = []
    positive_patterns = (
        re.compile(r"\b(?:is|was|has\s+been|have\s+been|successfully|now)\s+deployed\b", re.I),
        re.compile(r"\bdeployed\s+(?:to|in|on)\s+(?:the\s+)?production\b", re.I),
        re.compile(r"\b(?:is|was|now|currently)\s+live\s+(?:in|on|for|to)\b", re.I),
        re.compile(r"\bnow\s+available\b", re.I),
        re.compile(r"\b(?:is|was|currently)\s+available\s+(?:in|to|for)\b", re.I),
        re.compile(r"\brolled\s+out\s+(?:to|in|across)\b", re.I),
    )
    negative_pattern = re.compile(
        r"\b(?:not|never)\s+(?:been\s+)?(?:deployed|available|live|rolled\s+out)\b"
        r"|\bno\s+(?:production\s+)?(?:deployment|rollout|availability)\b"
        r"|\bdeployed\s*[=:]\s*false\b"
        r"|\bavailability\s+(?:is|was|has)\s+not\b",
        re.I,
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not sentence.strip() or negative_pattern.search(sentence):
            continue
        if any(pattern.search(sentence) for pattern in positive_patterns):
            claims.append(display_fact(sentence))
    return claims


def availability_overclaims(text: str) -> list[str]:
    claims: list[str] = []
    positive_patterns = (
        re.compile(r"\bnow\s+available\b", re.I),
        re.compile(r"\b(?:is|was|currently)\s+available\s+(?:in|to|for)\b", re.I),
        re.compile(r"\bavailability\s+(?:is|was)\s+confirmed\b", re.I),
    )
    negative_pattern = re.compile(
        r"\bnot\s+(?:currently\s+)?available\b"
        r"|\bno\s+(?:confirmed\s+)?availability\b"
        r"|\bavailable\s*[=:]\s*false\b"
        r"|\bavailability\s+(?:is|was|has)\s+not\b",
        re.I,
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", text):
        if not sentence.strip() or negative_pattern.search(sentence):
            continue
        if any(pattern.search(sentence) for pattern in positive_patterns):
            claims.append(display_fact(sentence))
    return claims


def validate_document(text: str, evidence: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    labels = heading_labels(text)
    mode = package_mode(evidence)

    required_sections = {
        "Press Release": ("press release",),
        "Headline": ("headline",),
        "Subheadline": ("subheadline",),
        "Dateline / Summary": ("dateline", "summary"),
        "Customer Problem": ("customer problem",),
        "Customer Experience": ("customer experience",),
        "Internal/staff quote": ("quote",),
        "Getting Started": ("getting started",),
        "Customer FAQ": ("customer faq",),
        "Internal FAQ": ("internal faq",),
    }
    if mode == RETROSPECTIVE_MODE:
        required_sections["Lifecycle outcome"] = ("lifecycle outcome",)
    else:
        required_sections["Review gate outcome"] = (
            "review gate outcome",
            "readiness gate outcome",
        )
    for name, aliases in required_sections.items():
        if not has_labeled_section(labels, aliases):
            failures.append(f"missing required section: {name}")

    normalized_text = normalize(text)
    if "illustrative" not in normalized_text and "sourced quote" not in normalized_text:
        failures.append(
            "quote must be identified as illustrative or as a sourced quote"
        )

    internal_text = normalize(find_section_tail(text, ("internal faq",)))
    internal_topics = {
        "exact scope and non-goals": (
            "scope" in internal_text
            and any(term in internal_text for term in ("non goal", "non goals", "out of scope"))
        ),
        "verification evidence": any(
            term in internal_text for term in ("verification", "checks", "tests")
        ),
        "authorization boundary": "authoriz" in internal_text,
        "safety, data, and migration impact": (
            "data" in internal_text and "migration" in internal_text
        ),
        "rollout or deployment truth": (
            "deployment" in internal_text
            and any(term in internal_text for term in ("rollout", "production", "available", "availability"))
        ),
        "rollback and observability": (
            "rollback" in internal_text and "observability" in internal_text
        ),
        "next human decision": (
            "human decision" in internal_text or "next decision" in internal_text
        ),
    }
    for topic, present in internal_topics.items():
        if not present:
            failures.append(f"Internal FAQ is missing required topic: {topic}")

    pr = evidence.get("pr")
    if not isinstance(pr, dict):
        pr = {}
    pr_number = pr.get("number")
    if pr_number is not None:
        pattern = re.compile(
            rf"(?:\bpr\s*(?:number\s*)?#?\s*{re.escape(str(pr_number))}\b|#{re.escape(str(pr_number))}\b)",
            re.I,
        )
        if not pattern.search(text):
            failures.append(f"missing required PR reference: PR #{pr_number}")

    issue_references = evidence.get("required_issue_references", [])
    if not isinstance(issue_references, list):
        failures.append("evidence required_issue_references must be a list")
        issue_references = []
    for issue in issue_references:
        pattern = re.compile(
            rf"(?:#{re.escape(str(issue))}\b|\bissue\s*#?\s*{re.escape(str(issue))}\b)",
            re.I,
        )
        if not pattern.search(text):
            failures.append(f"missing required issue reference: #{issue}")

    verification = evidence.get("verification")
    signatures = verification_signatures(verification)
    if signatures:
        for fact in signatures:
            if normalize(fact) not in normalized_text:
                failures.append(
                    f"missing verification fact from evidence: {display_fact(fact)}"
                )

    if mode == REVIEW_HANDOFF_MODE:
        if has_labeled_section(labels, ("lifecycle outcome",)):
            failures.append(
                "review_handoff package cannot use a RETROSPECTIVE lifecycle outcome"
            )

        blockers, exact_sha = readiness_blockers(evidence)
        expected_outcome = "BLOCKED" if blockers else "READY"
        actual_outcome = review_gate_outcome(text)
        if not actual_outcome:
            failures.append(
                "review gate outcome must state exactly one of READY or BLOCKED"
            )
        elif actual_outcome != expected_outcome:
            failures.append(
                f"review gate outcome is {actual_outcome}, but evidence requires "
                f"{expected_outcome}"
            )

        if blockers:
            if has_positive_ready_claim(text):
                failures.append("false ready claim: the review gate has blockers")
            for blocker in blockers:
                if normalize(blocker) not in normalized_text:
                    failures.append(
                        f"blocked package is missing exact blocker: {blocker}"
                    )

        if exact_sha and normalize(exact_sha) not in normalized_text:
            failures.append(f"package does not name the exact gate SHA: {exact_sha}")
        if not (
            "later commit" in normalized_text
            and "invalidates" in normalized_text
            and "gate" in normalized_text
        ):
            failures.append(
                "package must state that any later commit invalidates the gate"
            )
    else:
        if has_labeled_section(
            labels,
            ("review gate outcome", "readiness gate outcome"),
        ):
            failures.append(
                "retrospective package must not use a READY/BLOCKED review gate outcome"
            )
        actual_outcome = lifecycle_outcome(text)
        if actual_outcome != "RETROSPECTIVE":
            rendered = actual_outcome or "<missing>"
            failures.append(
                f"lifecycle outcome is {rendered}, but retrospective mode requires "
                "RETROSPECTIVE"
            )
        if has_positive_ready_claim(text):
            failures.append(
                "retrospective package must not claim current merge readiness"
            )

        state = str(pr.get("state") or "<missing>").upper()
        if state not in {"MERGED", "CLOSED"}:
            failures.append(
                f"retrospective mode requires PR state MERGED or CLOSED, got {state}"
            )
        elif not document_states_pr_state(text, state):
            failures.append(f"retrospective package must state PR state {state}")

        lifecycle = evidence.get("lifecycle")
        if not isinstance(lifecycle, dict):
            failures.append("retrospective evidence lifecycle object is missing")
            lifecycle = {}
        for fact in LIFECYCLE_FACTS:
            value = lifecycle.get(fact)
            if not isinstance(value, bool):
                failures.append(
                    f"retrospective evidence lifecycle.{fact} must be an explicit boolean"
                )
            elif not document_states_boolean_fact(text, fact, value):
                failures.append(
                    f"retrospective package must state {fact}: "
                    f"{'true' if value else 'false'}"
                )

        merged = lifecycle.get("merged")
        if state == "MERGED" and merged is not True:
            failures.append("MERGED PR requires lifecycle.merged: true")
        if state == "CLOSED" and merged is not False:
            failures.append("CLOSED PR requires lifecycle.merged: false")

    if deployment_is_false(evidence):
        for claim in deployment_overclaims(text):
            failures.append(
                "deployment/availability overclaim while evidence says deployed=false: "
                f"{claim}"
            )
    if availability_is_false(evidence):
        for claim in availability_overclaims(text):
            failures.append(
                "availability overclaim while evidence says available=false: "
                f"{claim}"
            )

    return failures


def self_test() -> int:
    sha = "0123456789abcdef0123456789abcdef01234567"
    evidence: dict[str, Any] = {
        "mode": REVIEW_HANDOFF_MODE,
        "commit": {
            "local_head": sha,
            "pushed_remote_branch_head": sha,
        },
        "pr": {
            "number": 42,
            "state": "OPEN",
            "isDraft": False,
            "mergeable": "MERGEABLE",
            "headRefOid": sha,
        },
        "authorization": {"pushed_branch": True, "open_pr": True},
        "required_issue_references": [7, 9],
        "verification": {
            "worker_gates": [
                {"name": "Worker gates", "conclusion": "SUCCESS", "sha": sha}
            ],
            "full_repository_gates": [
                {
                    "name": "12 test files and 340 tests",
                    "conclusion": "SUCCESS",
                    "sha": sha,
                }
            ],
            "independent_review": {
                "name": "Independent review",
                "conclusion": "No actionable findings",
                "sha": sha,
                "fresh": True,
            },
            "required_checks": [
                {"name": "CI gates", "conclusion": "SUCCESS", "sha": sha}
            ],
        },
        "safety": {"deployed": False, "available": False},
    }
    grounded = """# Press Release
## Headline
Teams find the right work
## Subheadline
The review package explains the staff experience.
## Dateline / Summary
New York — PR #42 covers #7 and #9 and is not deployed.
## Review Gate Outcome: READY
The exact gate SHA is 0123456789abcdef0123456789abcdef01234567. Any later commit invalidates this review gate.
## Customer Problem
Staff lacked context.
## Customer Experience
Staff can review the change.
## Illustrative Internal/Staff Quote
> “The context is clear.” — Illustrative staff perspective; not a sourced quote
## Getting Started
Wait for confirmed deployment.
# Customer FAQ
## What changed?
The reviewed experience is clearer.
## Is it available?
Production availability is not established.
# Internal FAQ
## What is the exact scope and what are the non-goals?
Scope is #7 and #9; deployment is a non-goal.
## What verification evidence supports this?
Worker gates SUCCESS 0123456789abcdef0123456789abcdef01234567.
12 test files and 340 tests SUCCESS 0123456789abcdef0123456789abcdef01234567.
Independent review No actionable findings 0123456789abcdef0123456789abcdef01234567.
CI gates SUCCESS 0123456789abcdef0123456789abcdef01234567.
Local HEAD, pushed remote branch head, and GitHub PR headRefOid are the same SHA. The OPEN PR is ready for review.
## What authorization boundary applies?
The pushed branch and open PR already exist under user authorization. Push, PR writes, merge, and deployment remain external actions requiring authorization.
## What is the safety, data, and migration impact?
No production data changed and no migration ran.
## What is true about rollout, deployment, and production availability?
It is not deployed; rollout and availability are not established.
## What rollback and observability evidence exists?
Rollback and observability must be confirmed before release.
## What is the next human decision?
The next human decision is whether to merge. Stop before merge.
"""

    grounded_failures = validate_document(grounded, evidence)
    if grounded_failures:
        print("SELF-TEST FAIL: READY sample should pass", file=sys.stderr)
        for failure in grounded_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    missing_mode_evidence = copy.deepcopy(evidence)
    del missing_mode_evidence["mode"]
    missing_mode_failures = validate_document(grounded, missing_mode_evidence)
    if missing_mode_failures:
        print(
            "SELF-TEST FAIL: missing mode should default to review_handoff",
            file=sys.stderr,
        )
        for failure in missing_mode_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    cases: list[tuple[str, str, dict[str, Any], str]] = []
    cases.append(
        (
            "missing evidence",
            grounded.replace(
                "12 test files and 340 tests SUCCESS",
                "Some tests passed",
            ),
            evidence,
            "missing verification fact",
        )
    )
    draft_evidence = copy.deepcopy(evidence)
    draft_evidence["pr"]["isDraft"] = True
    cases.append(("false ready claim", grounded, draft_evidence, "false ready claim"))
    unknown_evidence = copy.deepcopy(evidence)
    unknown_evidence["pr"]["mergeable"] = "UNKNOWN"
    cases.append(("unknown mergeability", grounded, unknown_evidence, "requires BLOCKED"))
    stale_evidence = copy.deepcopy(evidence)
    stale_evidence["verification"]["independent_review"]["sha"] = "f" * 40
    cases.append(("stale review", grounded, stale_evidence, "not exact SHA"))
    unclean_evidence = copy.deepcopy(evidence)
    unclean_evidence["verification"]["independent_review"]["conclusion"] = "not clean"
    cases.append(
        (
            "unclean review",
            grounded,
            unclean_evidence,
            "actionable or missing findings",
        )
    )
    overclaim = grounded.replace(
        "It is not deployed; rollout and availability are not established.",
        "It is deployed to production and now available to staff.",
    )
    cases.append(
        (
            "deployment overclaim",
            overclaim,
            evidence,
            "deployment/availability overclaim",
        )
    )

    blocked_evidence = copy.deepcopy(evidence)
    blocked_evidence["mode"] = "unknown-future-mode"
    blocked_evidence["pr"]["isDraft"] = True
    blockers, _ = readiness_blockers(blocked_evidence)
    blocked = grounded.replace(
        "## Review Gate Outcome: READY",
        "## Review Gate Outcome: BLOCKED",
    ).replace(
        "The OPEN PR is ready for review.",
        "The OPEN PR is blocked.",
    )
    blocked += "\n## Exact blockers\n" + "\n".join(f"- {item}" for item in blockers)
    blocked_failures = validate_document(blocked, blocked_evidence)
    if blocked_failures:
        print("SELF-TEST FAIL: blocked sample should pass", file=sys.stderr)
        for failure in blocked_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    retrospective_evidence: dict[str, Any] = {
        "mode": RETROSPECTIVE_MODE,
        "pr": {"number": 42, "state": "MERGED"},
        "required_issue_references": [7, 9],
        "lifecycle": {
            "built": True,
            "merged": True,
            "deployed": False,
            "available": False,
        },
    }
    retrospective = """# Press Release
## Headline
Teams can understand a historical change
## Subheadline
The package records what PR #42 changed without claiming a current launch.
## Dateline / Summary
Retrospective — PR #42 covered #7 and #9. PR state: MERGED.
## Lifecycle Outcome: RETROSPECTIVE
- Built: true — the historical diff contains the change.
- Merged: true — PR state: MERGED.
- Deployed: false — no production deployment evidence was supplied.
- Available: false — no confirmed availability evidence was supplied.
## Customer Problem
Staff lacked a durable explanation of the historical change.
## Customer Experience
Staff can review what was built and the limits of the lifecycle evidence.
## Illustrative Internal/Staff Quote
> “The historical record is clear.” — Illustrative staff perspective; not a sourced quote
## Getting Started
Treat this as history; confirm deployment and availability before use.
# Customer FAQ
## What changed?
The historical PR recorded a clearer experience.
## Is it available?
Availability is not established.
# Internal FAQ
## What is the exact scope and what are the non-goals?
Scope is #7 and #9; current merge readiness is a non-goal.
## What verification evidence supports this?
Historical verification, checks, and tests were not supplied; no current readiness is implied.
## What authorization boundary applies?
No historical open-PR authorization is required; any new external action requires user authorization.
## What is the safety, data, and migration impact?
No production data or migration evidence was supplied.
## What is true about rollout, deployment, and production availability?
Built: true. Merged: true. Deployed: false. Available: false. PR state: MERGED.
## What rollback and observability evidence exists?
No rollback or observability evidence was supplied.
## What is the next human decision?
The next human decision is whether to investigate deployment history, not whether to merge.
"""
    retrospective_failures = validate_document(
        retrospective,
        retrospective_evidence,
    )
    if retrospective_failures:
        print(
            "SELF-TEST FAIL: MERGED retrospective sample should pass",
            file=sys.stderr,
        )
        for failure in retrospective_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    closed_evidence = copy.deepcopy(retrospective_evidence)
    closed_evidence["pr"]["state"] = "CLOSED"
    closed_evidence["lifecycle"]["merged"] = False
    closed_retrospective = retrospective.replace(
        "PR state: MERGED",
        "PR state: CLOSED",
    ).replace(
        "Merged: true",
        "Merged: false",
    )
    closed_failures = validate_document(closed_retrospective, closed_evidence)
    if closed_failures:
        print(
            "SELF-TEST FAIL: CLOSED retrospective sample should pass",
            file=sys.stderr,
        )
        for failure in closed_failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    cases.append(
        (
            "merged retrospective labeled READY",
            retrospective.replace(
                "## Lifecycle Outcome: RETROSPECTIVE",
                "## Lifecycle Outcome: READY",
            ),
            retrospective_evidence,
            "requires RETROSPECTIVE",
        )
    )
    cases.append(
        (
            "open handoff labeled RETROSPECTIVE",
            grounded.replace(
                "## Review Gate Outcome: READY",
                "## Lifecycle Outcome: RETROSPECTIVE",
            ),
            evidence,
            "review_handoff package cannot use",
        )
    )
    retrospective_overclaim = retrospective.replace(
        "- Deployed: false — no production deployment evidence was supplied.",
        "- Deployed: false. It is deployed to production for staff.",
    )
    cases.append(
        (
            "retrospective deployment overclaim",
            retrospective_overclaim,
            retrospective_evidence,
            "deployment/availability overclaim",
        )
    )

    for name, document, case_evidence, expected in cases:
        failures = validate_document(document, case_evidence)
        if not failures or not any(expected in failure for failure in failures):
            print(
                f"SELF-TEST FAIL: bad case {name!r} did not produce {expected!r}",
                file=sys.stderr,
            )
            for failure in failures:
                print(f"  - {failure}", file=sys.stderr)
            return 1

    print(
        "PASS: self-test accepted 5 valid samples (explicit READY, default "
        "READY, unknown-mode BLOCKED, MERGED RETROSPECTIVE, and CLOSED "
        f"RETROSPECTIVE) and rejected {len(cases)} bad cases."
    )
    return 0


def load_text(path: Path, label: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise ValueError(f"cannot read {label} {path}: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate an Amazon-style PR/FAQ against JSON evidence."
    )
    parser.add_argument("faq_path", nargs="?", type=Path, metavar="FAQ_PATH")
    parser.add_argument("--evidence", type=Path, metavar="EVIDENCE_JSON")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        if args.faq_path is not None or args.evidence is not None:
            parser.error("--self-test does not accept FAQ_PATH or --evidence")
        return self_test()

    if args.faq_path is None:
        parser.error("FAQ_PATH is required unless --self-test is used")
    if args.evidence is None:
        parser.error("--evidence EVIDENCE_JSON is required")

    try:
        faq_text = load_text(args.faq_path, "FAQ")
        evidence_text = load_text(args.evidence, "evidence file")
        evidence = json.loads(evidence_text)
        if not isinstance(evidence, dict):
            raise ValueError("evidence JSON root must be an object")
    except (ValueError, json.JSONDecodeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 2

    failures = validate_document(faq_text, evidence)
    if failures:
        print(f"FAIL: {args.faq_path} did not validate:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1

    print(f"PASS: {args.faq_path} is grounded in {args.evidence}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
