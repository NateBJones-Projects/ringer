#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = (
    ROOT / ".claude" / "skills" / "amazon-pr-faq" / "scripts" / "validate_pr_faq.py"
)


def load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("amazon_pr_faq_validator", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load validator: {VALIDATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_validator()


def review_evidence(sha: str = "a" * 40) -> dict[str, Any]:
    return {
        "mode": "review_handoff",
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
        "verification": {
            "worker_gates": [
                {"name": "worker", "conclusion": "SUCCESS", "sha": sha}
            ],
            "full_repository_gates": [
                {"name": "full", "conclusion": "SUCCESS", "sha": sha}
            ],
            "independent_review": {
                "name": "review",
                "conclusion": "No actionable findings",
                "fresh": True,
                "sha": sha,
            },
            "required_checks": [
                {"name": "CI", "conclusion": "SUCCESS", "sha": sha}
            ],
        },
    }


def retrospective_evidence() -> dict[str, Any]:
    return {
        "mode": "retrospective",
        "pr": {"number": 42, "state": "MERGED"},
        "lifecycle": {
            "built": True,
            "merged": True,
            "deployed": False,
            "available": False,
        },
    }


class AmazonPrFaqValidatorTests(unittest.TestCase):
    def test_validator_self_test_runs_through_cli(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), "--self-test"],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("PASS: self-test", result.stdout)

    def test_only_full_sha1_or_sha256_can_establish_exact_sha(self) -> None:
        for valid_sha in ("a" * 40, "b" * 64):
            with self.subTest(valid_sha_length=len(valid_sha)):
                blockers, exact_sha = VALIDATOR.readiness_blockers(
                    review_evidence(valid_sha)
                )
                self.assertEqual([], blockers)
                self.assertEqual(valid_sha, exact_sha)

        for invalid_sha in (
            "abcdef1",
            "not-a-git-sha",
            "g" * 40,
            "a" * 39,
            "b" * 63,
        ):
            with self.subTest(invalid_sha=invalid_sha):
                blockers, exact_sha = VALIDATOR.readiness_blockers(
                    review_evidence(invalid_sha)
                )
                self.assertEqual("", exact_sha)
                self.assertTrue(blockers)
                self.assertTrue(
                    all(
                        "not a full 40- or 64-character hexadecimal Git object ID"
                        in blocker
                        for blocker in blockers
                    ),
                    blockers,
                )

    def test_invalid_sha_is_a_blocker_in_every_readiness_record(self) -> None:
        base_sha = "a" * 40
        invalid_sha = "abcdef1"
        locations = {
            "local HEAD": lambda item: item["commit"].__setitem__(
                "local_head", invalid_sha
            ),
            "remote head": lambda item: item["commit"].__setitem__(
                "pushed_remote_branch_head", invalid_sha
            ),
            "PR head": lambda item: item["pr"].__setitem__(
                "headRefOid", invalid_sha
            ),
            "worker gate": lambda item: item["verification"]["worker_gates"][
                0
            ].__setitem__("sha", invalid_sha),
            "full-repository gate": lambda item: item["verification"][
                "full_repository_gates"
            ][0].__setitem__("sha", invalid_sha),
            "independent review": lambda item: item["verification"][
                "independent_review"
            ].__setitem__("sha", invalid_sha),
            "required check": lambda item: item["verification"][
                "required_checks"
            ][0].__setitem__("sha", invalid_sha),
        }
        for name, mutate in locations.items():
            with self.subTest(location=name):
                evidence = copy.deepcopy(review_evidence(base_sha))
                mutate(evidence)
                blockers, _ = VALIDATOR.readiness_blockers(evidence)
                self.assertTrue(
                    any(
                        invalid_sha in blocker
                        and "not a full 40- or 64-character hexadecimal"
                        in blocker
                        for blocker in blockers
                    ),
                    blockers,
                )

    def test_review_conclusion_rejects_contradictory_clean_language(self) -> None:
        for conclusion in (
            "No actionable findings; changes requested",
            "No findings, but review failed",
            "No actionable findings; one critical issue remains",
            "No findings; merge should be blocked",
            "Approved with conditions",
            "Passed except for a blocker",
            "Clean, but changes requested",
        ):
            with self.subTest(contradictory_conclusion=conclusion):
                evidence = review_evidence()
                evidence["verification"]["independent_review"][
                    "conclusion"
                ] = conclusion
                blockers, _ = VALIDATOR.readiness_blockers(evidence)
                self.assertIn(
                    "independent review has actionable or missing findings",
                    blockers,
                )

        for conclusion in (
            "No actionable findings",
            "NO ACTIONABLE FINDINGS!",
            "No findings",
            "no FINDINGS.",
            "Clean",
            "cLeAn!",
            "Approved",
            "APPROVED.",
            "Passed",
            "passed!",
        ):
            with self.subTest(clean_conclusion=conclusion):
                evidence = review_evidence()
                evidence["verification"]["independent_review"][
                    "conclusion"
                ] = conclusion
                blockers, exact_sha = VALIDATOR.readiness_blockers(evidence)
                self.assertEqual([], blockers)
                self.assertEqual("a" * 40, exact_sha)

    def test_readiness_claim_negation_is_phrase_scoped(self) -> None:
        positive_claims = (
            "This PR is not deployed and is ready to merge.",
            "This PR is ready.",
            "Proceed with the merge.",
            "Proceed to merge.",
            "This PR is safe to merge.",
            "This PR is approved to merge.",
            "This PR is clean and mergeable today.",
            "The merge can proceed.",
            "This PR can be merged now.",
            "Mergeable.",
            "This PR is cleared to merge.",
            "The merge is approved.",
        )
        for claim in positive_claims:
            with self.subTest(positive_claim=claim):
                self.assertTrue(VALIDATOR.has_positive_ready_claim(claim))

        for claim in (
            "This PR is not ready to merge.",
            "This PR is not ready.",
            "This PR isn't ready.",
            "Do not proceed with the merge.",
            "Don't proceed to merge.",
            "Do not call this safe to merge.",
            "This PR should not be considered ready to merge.",
            "This PR should never be called safe to merge.",
            "This PR is ready for review.",
            "This PR is ready for human review.",
            "Is this PR ready?",
            "Should we proceed with the merge?",
            "Proceed to merge?",
        ):
            with self.subTest(negated_claim=claim):
                self.assertFalse(VALIDATOR.has_positive_ready_claim(claim))

        blocked_evidence = review_evidence()
        blocked_evidence["pr"]["isDraft"] = True
        for claim in positive_claims:
            failures = VALIDATOR.validate_document(claim, blocked_evidence)
            self.assertIn(
                "false ready claim: the review gate has blockers",
                failures,
            )
        negated_failures = VALIDATOR.validate_document(
            "This PR is not ready. Do not proceed with the merge. "
            "Don't proceed to merge. This PR is ready for review. "
            "This PR is not ready to merge. Do not call this safe to merge. "
            "This PR should not be considered ready to merge. "
            "This PR should never be called safe to merge.",
            blocked_evidence,
        )
        self.assertNotIn(
            "false ready claim: the review gate has blockers",
            negated_failures,
        )

    def test_retrospective_rejects_plain_outcomes_and_current_readiness(self) -> None:
        evidence = retrospective_evidence()
        for label in (
            "Review Gate Outcome: READY",
            "Readiness Gate Outcome: BLOCKED",
        ):
            with self.subTest(label=label):
                failures = VALIDATOR.validate_document(label, evidence)
                self.assertIn(
                    "retrospective package must not use a READY/BLOCKED "
                    "review gate outcome",
                    failures,
                )

        for claim in (
            "This PR is not deployed and is ready to merge.",
            "This PR is ready.",
            "Proceed with the merge.",
            "Proceed to merge.",
            "This PR is safe to merge.",
            "This PR is approved to merge.",
            "This PR is clean and mergeable today.",
            "The merge can proceed.",
            "This PR can be merged now.",
            "Mergeable.",
            "This PR is cleared to merge.",
            "The merge is approved.",
        ):
            with self.subTest(claim=claim):
                failures = VALIDATOR.validate_document(claim, evidence)
                self.assertIn(
                    "retrospective package must not claim current merge readiness",
                    failures,
                )

        negated_failures = VALIDATOR.validate_document(
            "This PR is not ready. Do not proceed with the merge. "
            "Don't proceed to merge. This PR is ready for review. "
            "This PR is not ready to merge. Do not call this safe to merge. "
            "This PR should not be considered ready to merge. "
            "This PR should never be called safe to merge.",
            evidence,
        )
        self.assertNotIn(
            "retrospective package must not claim current merge readiness",
            negated_failures,
        )

    def test_deployment_negation_is_phrase_scoped(self) -> None:
        claim = "It is not available, but it is deployed to production."
        failures = VALIDATOR.validate_document(claim, retrospective_evidence())
        self.assertTrue(
            any("deployment/availability overclaim" in item for item in failures),
            failures,
        )
        self.assertEqual([], VALIDATOR.deployment_overclaims("It is not deployed."))
        self.assertEqual(
            [],
            VALIDATOR.availability_overclaims("It is not available to staff."),
        )

    def test_common_deployment_and_availability_claims_are_rejected(self) -> None:
        claims = (
            "Available now.",
            "Deployment completed successfully.",
            "Shipped to production.",
            "Live now.",
            "The feature is available.",
            "The feature is live.",
            "The deployment is complete.",
        )
        for claim in claims:
            with self.subTest(claim=claim):
                failures = VALIDATOR.validate_document(
                    claim,
                    retrospective_evidence(),
                )
                self.assertTrue(
                    any("overclaim" in failure for failure in failures),
                    failures,
                )

        self.assertEqual(
            [],
            VALIDATOR.availability_overclaims("Is this available now?"),
        )
        self.assertEqual(
            [],
            VALIDATOR.deployment_overclaims("Deployment completed successfully?"),
        )
        for claim in (
            "The feature is not available.",
            "The feature isn't available.",
            "The feature is not live.",
            "The feature isn't live.",
            "The deployment is not complete.",
            "The deployment isn't complete.",
            "Is the feature available?",
            "Is the feature live?",
            "Is the deployment complete?",
        ):
            with self.subTest(non_claim=claim):
                self.assertEqual([], VALIDATOR.deployment_overclaims(claim))
                self.assertEqual([], VALIDATOR.availability_overclaims(claim))
        self.assertFalse(VALIDATOR.has_positive_ready_claim("Mergeable?"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
