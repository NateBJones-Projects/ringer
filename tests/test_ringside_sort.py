#!/usr/bin/env python3
"""Ringside artifact picker sorts finished runs by recency, newest first.

The picker groups live runs above finished ones; within the finished group
the old code fell back to alphabetical name order, so the newest finished
run rarely sat on top. The comparator now orders finished runs by
`updatedTs` descending while keeping live runs name-sorted (two in-flight
runs must not swap rows as their "updated Ns ago" ticks each flush).

Ringer's suite has no JS runtime, so this is a structural regression guard
on the shipped comparator in dashboard/ringside.html — the same style as
test_signal_contract's assertions on that file. It targets the picker
comparator unambiguously by anchoring on `right.updatedTs`, which the other
leftLive/rightLive sort on the page does not use.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import ringer  # noqa: E402


class RingsideSortTests(unittest.TestCase):
    def setUp(self) -> None:
        html = ringer.read_ringside_html()
        # Anchor on the recency comparison (unique to the picker comparator),
        # then back up to the start of that sort callback.
        anchor = html.index("right.updatedTs")
        start = html.rindex("(left, right) => {", 0, anchor)
        self.comparator = html[start : anchor + 200]

    def test_live_runs_are_grouped_above_finished(self) -> None:
        self.assertIn("rightLive - leftLive", self.comparator)

    def test_finished_runs_sort_by_recency_newest_first(self) -> None:
        # newest first == updatedTs descending (right minus left).
        self.assertRegex(self.comparator, r"right\.updatedTs\s*-\s*left\.updatedTs")

    def test_live_runs_stay_name_sorted_for_stability(self) -> None:
        # A live-only guard returning localeCompare keeps two in-flight runs
        # from swapping rows every flush.
        self.assertRegex(
            self.comparator,
            r"if\s*\(\s*leftLive\s*\)\s*return\s+left\.name\.localeCompare",
        )

    def test_recency_compare_precedes_the_name_tiebreak(self) -> None:
        # Regression: the finished group must not be name-only sorted. The
        # recency comparison must come before the trailing name tiebreak.
        recency = self.comparator.index("right.updatedTs")
        self.assertIn("localeCompare", self.comparator[recency:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
