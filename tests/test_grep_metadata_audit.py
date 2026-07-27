#!/usr/bin/env python3
"""EXECUTED metadata audit for the grep/rg option tables.

parse_grep (ringer.py) decides whether an absence-grep lint should fire by
consulting _GREP_SPEC / _RG_SPEC: hand-maintained tables of which options a
command recognizes and with what ARITY. Those tables carry an INTERSECTION
CONTRACT -- every positively-listed option must be valid, with identical arity,
under EVERY implementation a manifest may run under (GNU grep on Linux, BSD grep
on macOS; ripgrep across its stable releases). A table that drifts out of the
intersection re-opens the false-fire finding class the contract exists to close.

This suite enforces the contract in TWO layers:

(1) COVERAGE -- BINARY-INDEPENDENT (test_metadata_coverage_is_probeable). Runs on
    every machine, installed binary or not, and NEVER skips. It asserts that the
    declared arity classes partition cleanly AND that every positively-listed
    value-taking option (long or short) has an audit dummy value defined below.
    A listed option with no probe definition fails the suite everywhere: a probe
    table that silently fails to cover a listed option can no longer masquerade
    as full coverage.

(2) ARITY DISCRIMINATION -- BINARY-GATED (test_{grep,rg}_metadata_matches_local
    _binary). When the real binary is on PATH, every listed option is EXECUTED
    with control invocations chosen to discriminate its DECLARED arity class from
    the others, and the test fails if the binary's behavior indicates a different
    class. Merely "the parser accepted the option" is NOT enough (a required-value
    option silently swallows a probe operand; a boolean silently ignores a
    trailing dummy) -- so each class is pinned by a POSITIVE and a NEGATIVE
    control. Only these executions skip per-binary; the coverage layer above does
    not. Because the suite runs on macOS (BSD grep) here and on Linux (GNU grep)
    in CI, an option invalid or mis-classed on either implementation fails there.

Every probe is read-only and instant: it searches ONLY /dev/null (never the
repo), reads its stdin from /dev/null (so a bare boolean/optional probe cannot
block on a terminal), and uses constant dummy values chosen per option.

SIGNAL VOCABULARY (see _signal), all verified against the LOCAL binaries -- BSD
grep 2.6.0-FreeBSD and ripgrep 15.1.0 -- and against GNU grep's documented
messages:
  UNREC  exit 2 + unrecognized-option signature. grep: "unrecognized option" /
         "invalid option"; rg: "unrecognized flag" / "unrecognized option". The
         parser rejected the spelling outright.
  REQ    exit 2 + missing-argument signature, DISTINCT from UNREC. grep:
         "requires an argument"; rg: "missing value for flag" / "missing argument
         for option". The option is known and DEMANDS a value.
  REJVAL exit 2 + rejects-a-value signature. grep: "doesn't allow an argument";
         rg: "unexpected argument for option". The option is known and takes NO
         value (an inline '=v' was refused).
  ACCEPT anything else -- exit 0/1 (match / no match) or any OTHER exit-2
         diagnostic (missing file operand, bad WHEN value, usage banner). The
         parser accepted the option at the probed shape and moved on.

Class -> controls (positive / negative):
  required-value  bare '--opt'            -> REQ     (demands a value; !=boolean)
                  '--opt VAL [x] /dev/null'-> ACCEPT  (recognized at this arity)
  boolean         '--opt x /dev/null'     -> ACCEPT  (recognized, no value)
                  '--opt=v x /dev/null'   -> REJVAL  (refuses an inline value)
  optional-value  '--opt x /dev/null'     -> ACCEPT  (recognized)
                  bare '--opt'            -> not REQ (value is optional)
                  '--opt=VAL x /dev/null' -> ACCEPT  (accepts an inline value;
                                                      a boolean would REJVAL)
Short flags mirror this: value_short bare -> REQ and '-X VAL x /dev/null' ->
ACCEPT; boolean_short bare -> not REQ/UNREC and '-X x /dev/null' -> recognized.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ringer import _GREP_SPEC, _RG_SPEC  # noqa: E402

# Short flags that SUPPLY THE PATTERN rather than take an ordinary value
# (-e/-f). Probing them cleanly means threading a real pattern/pattern-file and
# suppressing the implicit pattern operand; the long forms --regexp/--file are
# audited instead (they exercise the same value-consuming code path), so the
# short probes are skipped here. Intentional and documented per the contract;
# the coverage layer explicitly exempts these two (and only these two).
_SKIP_SHORT = {"e", "f"}

# Dummy VALUES for each REQUIRED-VALUE long option, chosen so the only way a
# probe fails with a missing/invalid-argument signature is genuine mis-arity,
# never a bad-value diagnostic (e.g. --devices takes an ACTION, --color a WHEN).
_GREP_LONG_VALUES = {
    "regexp": "x",            # pattern
    "file": "/dev/null",      # empty pattern file
    "max-count": "1",
    "after-context": "1",
    "before-context": "1",
    "include": "x",
    "exclude": "x",
    "exclude-dir": "x",
    "devices": "skip",        # ACTION: read|skip|recurse
    "directories": "skip",    # ACTION: read|skip|recurse
    "binary-files": "text",   # TYPE: binary|text|without-match
    "label": "x",
}
# OPTIONAL-VALUE long options bind a value only via '--opt=VAL'; the dummy must
# be a VALID inline value so the '=VAL' control is ACCEPT (a boolean would
# REJVAL, a required option would not reach here). GNU/BSD grep --color/--colour
# take a WHEN; 'never' is valid on both.
_GREP_OPTIONAL_VALUES = {
    "color": "never",
    "colour": "never",
}
_GREP_SHORT_VALUES = {
    "m": "1",
    "A": "1",
    "B": "1",
    "C": "1",
    "D": "skip",   # --devices action
    "d": "skip",   # --directories action
}
_RG_LONG_VALUES = {
    "regexp": "x",
    "file": "/dev/null",
    "max-count": "1",
    "after-context": "1",
    "before-context": "1",
    "context": "1",            # rg --context requires a value (unlike GNU grep)
    "glob": "x",
    "iglob": "x",
    "type": "py",
    "type-not": "py",
    "type-add": "foo:*.xyz",   # format name:glob
    "type-clear": "py",
    "max-columns": "1",
    "replace": "x",
    "encoding": "utf-8",
    "max-depth": "1",
    "threads": "1",
    "sort": "path",            # none|path|modified|accessed|created
    "sortr": "path",
    "pre": "cat",              # a preprocessor command
    "ignore-file": "/dev/null",
    "color": "never",          # never|auto|always|ansi
    "colors": "path:fg:red",   # {type}:{attr}:{value}
}
# ripgrep has no optional-argument long options (verified vs rg 15.1.0).
_RG_OPTIONAL_VALUES: dict = {}
_RG_SHORT_VALUES = {
    "m": "1",
    "A": "1",
    "B": "1",
    "C": "1",
    "g": "x",
    "t": "py",
    "T": "py",
    "M": "1",
    "r": "x",
    "E": "utf-8",
    "j": "1",
}

# Per-command registry so BOTH specs are audited from one loop.
_SPECS = {"grep": _GREP_SPEC, "rg": _RG_SPEC}
_PROBES = {
    "grep": {
        "long": _GREP_LONG_VALUES,
        "optional": _GREP_OPTIONAL_VALUES,
        "short": _GREP_SHORT_VALUES,
    },
    "rg": {
        "long": _RG_LONG_VALUES,
        "optional": _RG_OPTIONAL_VALUES,
        "short": _RG_SHORT_VALUES,
    },
}


def _signal(kind: str, returncode: int, stderr: str) -> str:
    """Classify a probe run as one of UNREC / REQ / REJVAL / ACCEPT.

    Only exit status 2 carries an option diagnostic; exit 0/1 (match/no match)
    and any non-option exit-2 (missing file operand, bad WHEN value, usage
    banner) are ACCEPT -- the parser took the option and moved on. The three
    error signatures are mutually exclusive in practice and are checked in
    priority order. Signatures verified against BSD grep 2.6.0-FreeBSD and
    ripgrep 15.1.0; GNU grep uses the same "requires an argument" / "doesn't
    allow an argument" / "unrecognized option" wording."""
    if returncode != 2:
        return "ACCEPT"
    low = stderr.lower()
    if kind == "rg":
        if "unrecognized flag" in low or "unrecognized option" in low:
            return "UNREC"
        if "missing value for flag" in low or "missing argument for option" in low:
            return "REQ"
        if "unexpected argument for option" in low:
            return "REJVAL"
        return "ACCEPT"
    # grep (BSD + GNU)
    if "unrecognized option" in low or "invalid option" in low:
        return "UNREC"
    if "requires an argument" in low:
        return "REQ"
    if "doesn't allow an argument" in low:
        return "REJVAL"
    return "ACCEPT"


def _run(binary: str, args: list) -> subprocess.CompletedProcess:
    return subprocess.run(
        [binary, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,  # a bare boolean/optional probe must not block
        text=True,
        timeout=15,
    )


class GrepMetadataCoverageTest(unittest.TestCase):
    """BINARY-INDEPENDENT. Never skips: asserts the arity classes partition and
    that every listed value-taking option has an audit dummy, so a missing probe
    definition fails the suite on every machine, binary present or not."""

    def test_metadata_coverage_is_probeable(self) -> None:
        for cmd, spec in _SPECS.items():
            probes = _PROBES[cmd]
            long_values, optional_values, short_values = (
                probes["long"], probes["optional"], probes["short"]
            )
            # (1) The declared classes must be a clean partition, so every option
            # has exactly one arity class to probe against.
            with self.subTest(cmd=cmd, check="partition"):
                self.assertEqual(
                    spec.value_long & spec.boolean_long, set(),
                    f"{cmd}: options declared BOTH value_long and boolean_long",
                )
                self.assertEqual(
                    spec.value_long & spec.optional_long, set(),
                    f"{cmd}: options declared BOTH value_long and optional_long",
                )
                self.assertEqual(
                    spec.boolean_long & spec.optional_long, set(),
                    f"{cmd}: options declared BOTH boolean_long and optional_long",
                )
                self.assertLessEqual(
                    spec.pattern_long, spec.value_long,
                    f"{cmd}: pattern_long options must also be value_long (they "
                    f"consume the pattern as a required value)",
                )
                self.assertEqual(
                    spec.boolean_short & spec.value_short, set(),
                    f"{cmd}: short flags declared BOTH boolean and value-taking",
                )
            # (2) Every required-value LONG option needs an audit dummy.
            for opt in sorted(spec.value_long):
                with self.subTest(cmd=cmd, value_long=opt):
                    self.assertIn(
                        opt, long_values,
                        f"{cmd}: required-value long option --{opt} has no audit "
                        f"dummy in the long-values table; add one so the arity "
                        f"audit executes it against the real binary",
                    )
            # (3) Every optional-value LONG option needs a VALID inline dummy.
            for opt in sorted(spec.optional_long):
                with self.subTest(cmd=cmd, optional_long=opt):
                    self.assertIn(
                        opt, optional_values,
                        f"{cmd}: optional-value long option --{opt} has no audit "
                        f"dummy in the optional-values table; add a valid inline "
                        f"value so the '--{opt}=VAL' control can run",
                    )
            # (4) Every required-value SHORT option (except pattern-supplying
            # -e/-f) needs an audit dummy.
            for ch in sorted(spec.value_short):
                if ch in _SKIP_SHORT:
                    continue
                with self.subTest(cmd=cmd, value_short=ch):
                    self.assertIn(
                        ch, short_values,
                        f"{cmd}: required-value short option -{ch} has no audit "
                        f"dummy; add one (or add it to _SKIP_SHORT with a reason)",
                    )


class GrepMetadataAuditTest(unittest.TestCase):
    """BINARY-GATED. Executes the real binary for every listed option and fails
    if its observed arity class differs from the declared one."""

    def _binary(self, name: str) -> str:
        path = shutil.which(name)
        if path is None:
            self.skipTest(f"{name} not on PATH; arity execution needs the real binary")
        return path

    def _probe(self, kind: str, binary: str, args: list):
        proc = _run(binary, args)
        return _signal(kind, proc.returncode, proc.stderr), proc

    def _msg(self, cmd: str, args: list, got: str, want: str, proc, why: str) -> str:
        return (
            f"{cmd} `{cmd} {' '.join(args)}` -> signal {got}, expected {want}. {why}. "
            f"(exit {proc.returncode}: {proc.stderr.strip()!r}). This violates the "
            f"INTERSECTION CONTRACT: the option's declared arity in the metadata "
            f"disagrees with this implementation -- fix the arity, or prune the option."
        )

    def _assert_signal(self, kind, binary, cmd, args, want, why) -> None:
        got, proc = self._probe(kind, binary, args)
        self.assertEqual(got, want, self._msg(cmd, args, got, want, proc, why))

    def _assert_not_signal(self, kind, binary, cmd, args, unwanted, why) -> None:
        got, proc = self._probe(kind, binary, args)
        self.assertNotEqual(got, unwanted, self._msg(cmd, args, got, f"not {unwanted}", proc, why))

    def _audit_long(self, kind, binary, cmd, spec, opt, probes) -> None:
        dashed = "--" + opt
        if opt in spec.value_long:  # REQUIRED value (includes pattern_long)
            val = probes["long"][opt]
            self._assert_signal(
                kind, binary, cmd, [dashed], "REQ",
                "declared REQUIRED-VALUE but the bare option did not demand an "
                "argument (a boolean would be ACCEPT, an unknown option UNREC)",
            )
            operands = [val, "/dev/null"] if opt in spec.pattern_long else [val, "x", "/dev/null"]
            self._assert_signal(
                kind, binary, cmd, [dashed, *operands], "ACCEPT",
                "declared REQUIRED-VALUE but the binary rejected it at that arity",
            )
        elif opt in spec.optional_long:  # OPTIONAL value (binds only via =VAL)
            val = probes["optional"][opt]
            self._assert_signal(
                kind, binary, cmd, [dashed, "x", "/dev/null"], "ACCEPT",
                "declared OPTIONAL-VALUE but the binary did not accept the bare form",
            )
            self._assert_not_signal(
                kind, binary, cmd, [dashed], "REQ",
                "declared OPTIONAL-VALUE but the bare option DEMANDS a value (looks "
                "REQUIRED)",
            )
            self._assert_signal(
                kind, binary, cmd, [f"{dashed}={val}", "x", "/dev/null"], "ACCEPT",
                "declared OPTIONAL-VALUE but the binary refused an inline value "
                "(looks BOOLEAN)",
            )
        else:  # BOOLEAN (takes no value)
            self._assert_signal(
                kind, binary, cmd, [dashed, "x", "/dev/null"], "ACCEPT",
                "declared BOOLEAN but the binary did not recognize/accept the bare form",
            )
            self._assert_signal(
                kind, binary, cmd, [f"{dashed}=v", "x", "/dev/null"], "REJVAL",
                "declared BOOLEAN but the binary ACCEPTED an inline value (it "
                "actually takes one)",
            )

    def _audit_value_short(self, kind, binary, cmd, ch, probes) -> None:
        dashed = "-" + ch
        val = probes["short"][ch]
        self._assert_signal(
            kind, binary, cmd, [dashed], "REQ",
            "declared REQUIRED-VALUE short flag but the bare form did not demand a value",
        )
        self._assert_signal(
            kind, binary, cmd, [dashed, val, "x", "/dev/null"], "ACCEPT",
            "declared REQUIRED-VALUE short flag but rejected at that arity",
        )

    def _audit_boolean_short(self, kind, binary, cmd, ch) -> None:
        dashed = "-" + ch
        # A boolean short flag must be recognized and demand NO value: the bare
        # form is anything but REQ/UNREC, and the flag-plus-operands form parses.
        self._assert_not_signal(
            kind, binary, cmd, [dashed], "REQ",
            "declared BOOLEAN short flag but the bare form DEMANDS a value",
        )
        self._assert_not_signal(
            kind, binary, cmd, [dashed], "UNREC",
            "declared BOOLEAN short flag but the binary does not recognize it",
        )
        self._assert_not_signal(
            kind, binary, cmd, [dashed, "x", "/dev/null"], "UNREC",
            "declared BOOLEAN short flag but the binary does not recognize it",
        )

    def _audit_command(self, kind: str, cmd_name: str) -> None:
        binary = self._binary(cmd_name)  # per-binary skip lives HERE only
        spec = _SPECS[cmd_name]
        probes = _PROBES[cmd_name]
        long_names = (
            spec.value_long | spec.optional_long | spec.boolean_long | spec.pattern_long
        )
        for opt in sorted(long_names):
            with self.subTest(cmd=cmd_name, long=opt):
                self._audit_long(kind, binary, cmd_name, spec, opt, probes)
        for ch in sorted(spec.value_short):
            if ch in _SKIP_SHORT:
                continue
            with self.subTest(cmd=cmd_name, value_short=ch):
                self._audit_value_short(kind, binary, cmd_name, ch, probes)
        for ch in sorted(spec.boolean_short):
            with self.subTest(cmd=cmd_name, boolean_short=ch):
                self._audit_boolean_short(kind, binary, cmd_name, ch)

    def test_grep_metadata_matches_local_binary(self) -> None:
        self._audit_command("grep", "grep")

    def test_rg_metadata_matches_local_binary(self) -> None:
        self._audit_command("rg", "rg")


if __name__ == "__main__":
    unittest.main(verbosity=2)
