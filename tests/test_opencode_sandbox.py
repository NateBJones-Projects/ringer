#!/usr/bin/env python3
"""Tests for engines/opencode-sandboxed.sh: the --writable-root wrapper option.

These tests are deterministic, offline, and free of model calls. They put a
fake `opencode` on PATH that logs the argv it received and attempts file writes
to caller-supplied candidate directories, reporting WROTE/DENIED per path.

Seatbelt-specific assertions (writes actually confined by sandbox-exec) run only
where sandbox-exec is present AND can actually apply a profile; otherwise they
are skipped while the argument-parsing, validation, forwarding, and bash -n
tests still run.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "engines" / "opencode-sandboxed.sh"

# A fake `opencode` placed on PATH. It:
#   * records its argv (so we can prove wrapper options are not forwarded);
#   * attempts a file write into each newline-separated path in FAKE_OC_TRIES,
#     printing "WROTE\t<path>" / "DENIED\t<path>";
#   * attempts a write into the wrapper's scratch dir (its TMPDIR), printing
#     "WROTE\tSCRATCH" / "DENIED\tSCRATCH" (proves the scratch allowance holds);
#   * exits with FAKE_OC_EXIT (default 0).
# Written as a raw string so its own "\n"/"\t" escapes survive into the file.
FAKE_OC = r'''#!/usr/bin/env python3
import os, sys, json
log = os.environ.get("FAKE_OC_LOG")
if log:
    with open(log, "w") as f:
        json.dump({"argv": sys.argv[1:]}, f)
for line in os.environ.get("FAKE_OC_TRIES", "").split("\n"):
    p = line
    if not p:
        continue
    try:
        with open(os.path.join(p, "w.txt"), "w") as fh:
            fh.write("ok")
        print("WROTE\t" + p)
    except OSError:
        print("DENIED\t" + p)
td = os.environ.get("TMPDIR", "")
if td:
    try:
        with open(os.path.join(td, "scratch.txt"), "w") as fh:
            fh.write("ok")
        print("WROTE\tSCRATCH")
    except OSError:
        print("DENIED\tSCRATCH")
sys.exit(int(os.environ.get("FAKE_OC_EXIT", "0")))
'''


def _sandbox_exec_path() -> str | None:
    for cand in (shutil.which("sandbox-exec"), "/usr/bin/sandbox-exec"):
        if cand and os.path.exists(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _sandbox_functional() -> str | None:
    """Return the sandbox-exec path only if it can actually apply a profile.

    sandbox-exec may be present as a binary yet unable to apply containment (for
    example, when this process is already itself sandboxed). Probe by building a
    profile that denies all writes except one allow-listed dir, then asserting a
    write to a blocked dir is denied with EPERM.
    """
    sx = _sandbox_exec_path()
    if not sx:
        return None
    d = tempfile.mkdtemp(prefix="sb-probe-")
    try:
        allowed = os.path.join(d, "allowed")
        os.makedirs(allowed)
        blocked = os.path.join(d, "blocked")
        os.makedirs(blocked)
        prof = os.path.join(d, "prof")
        with open(prof, "w") as f:
            f.write(
                '(version 1)\n(allow default)\n(deny file-write*)\n'
                '(allow file-write* (subpath (param "A")))\n'
            )
        probe = os.path.join(d, "probe.py")
        with open(probe, "w") as f:
            f.write(
                'import os, sys\n'
                'p = os.path.join(sys.argv[1], "blocked", "x.txt")\n'
                'try:\n'
                '    open(p, "w").write("y"); print("DENIED-YES-UNEXPECTED")\n'
                'except OSError:\n'
                '    print("DENIED-YES")\n'
            )
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        r = subprocess.run(
            [sx, "-D", "A=" + allowed, "-f", prof, sys.executable, probe, d],
            capture_output=True, text=True, env=env, timeout=30,
        )
        if r.returncode == 0 and "DENIED-YES" in r.stdout.splitlines():
            return sx
        return None
    except Exception:
        return None
    finally:
        shutil.rmtree(d, ignore_errors=True)


SEATBELT = _sandbox_functional()
SEATBELT_REASON = (
    "sandbox-exec not present or cannot apply a containment profile "
    "in this environment"
)


class OpenCodeSandboxWrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.base = tempfile.mkdtemp(prefix="oc-sandbox-tests-")
        self.bindir = os.path.join(self.base, "bin")
        os.makedirs(self.bindir)
        fake = os.path.join(self.bindir, "opencode")
        with open(fake, "w") as f:
            f.write(FAKE_OC)
        os.chmod(fake, 0o755)
        self.taskdir = os.path.join(self.base, "taskdir")
        self.rootA = os.path.join(self.base, "rootA")
        self.rootB = os.path.join(self.base, "rootB")
        self.outside = os.path.join(self.base, "outside")
        self.spaces = os.path.join(self.base, "sp ced (and) parens")
        for p in (self.taskdir, self.rootA, self.rootB, self.outside, self.spaces):
            os.makedirs(p)
        self.logpath = os.path.join(self.taskdir, "oc-log.json")

    def tearDown(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)

    # ---- helpers -----------------------------------------------------------

    def _env(self, tries=None, exit_code=None, with_log=True) -> dict:
        env = dict(os.environ)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if with_log:
            env["FAKE_OC_LOG"] = self.logpath
        if tries is not None:
            env["FAKE_OC_TRIES"] = "\n".join(tries)
        if exit_code is not None:
            env["FAKE_OC_EXIT"] = str(exit_code)
        return env

    def _run(self, args, tries=None, exit_code=None, with_log=True):
        return subprocess.run(
            ["/bin/bash", str(WRAPPER), *args],
            capture_output=True, text=True,
            env=self._env(tries=tries, exit_code=exit_code, with_log=with_log),
            timeout=120,
        )

    @staticmethod
    def _parse(out: str) -> dict:
        res = {}
        for line in out.splitlines():
            if "\t" in line:
                marker, path = line.split("\t", 1)
                res[path] = marker
        return res

    def _argv(self) -> list:
        with open(self.logpath) as f:
            return json.load(f)["argv"]

    # ---- always-on: syntax, parsing, validation, forwarding ----------------

    def test_bash_n_syntax_clean(self) -> None:
        r = subprocess.run(["/bin/bash", "-n", str(WRAPPER)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, "bash -n reported a syntax error:\n" + r.stderr)

    def test_missing_writable_root_argument_errors_clearly(self) -> None:
        r = self._run([self.taskdir, "--writable-root"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--writable-root", r.stderr)

    def test_non_directory_writable_root_errors_clearly(self) -> None:
        afile = os.path.join(self.base, "afile.txt")
        with open(afile, "w") as f:
            f.write("x")
        r = self._run([self.taskdir, "--writable-root", afile, "run"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a directory", r.stderr)

    def test_nonexistent_writable_root_errors_clearly(self) -> None:
        missing = os.path.join(self.base, "does-not-exist-xyz")
        r = self._run([self.taskdir, "--writable-root", missing, "run"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("not a directory", r.stderr)

    def test_no_sandbox_strips_writable_root_option(self) -> None:
        r = self._run(
            [self.taskdir, "--no-sandbox", "--writable-root", self.rootA, "run", "--distinct"]
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = self._argv()
        self.assertEqual(argv, ["run", "--distinct"])
        self.assertNotIn("--writable-root", argv)
        self.assertNotIn(self.rootA, argv)

    def test_no_sandbox_missing_root_arg_still_errors(self) -> None:
        # Validation is mode-independent: a missing root fails even in full-access.
        r = self._run([self.taskdir, "--no-sandbox", "--writable-root"])
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--writable-root", r.stderr)

    def test_no_sandbox_runs_without_any_writable_root(self) -> None:
        r = self._run([self.taskdir, "--no-sandbox", "run", "--q"])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._argv(), ["run", "--q"])

    # ---- Seatbelt-specific: real write confinement -------------------------

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_taskdir_write_succeeds(self) -> None:
        r = self._run([self.taskdir, "run"], tries=[self.taskdir])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._parse(r.stdout).get(self.taskdir), "WROTE")
        self.assertTrue(os.path.exists(os.path.join(self.taskdir, "w.txt")))

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_declared_writable_root_write_succeeds(self) -> None:
        r = self._run([self.taskdir, "--writable-root", self.rootA, "run"], tries=[self.rootA])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._parse(r.stdout).get(self.rootA), "WROTE")
        self.assertTrue(os.path.exists(os.path.join(self.rootA, "w.txt")))

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_write_outside_taskdir_and_roots_is_denied(self) -> None:
        r = self._run(
            [self.taskdir, "--writable-root", self.rootA, "run"],
            tries=[self.taskdir, self.rootA, self.outside],
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        res = self._parse(r.stdout)
        self.assertEqual(res.get(self.taskdir), "WROTE")
        self.assertEqual(res.get(self.rootA), "WROTE")
        self.assertEqual(res.get(self.outside), "DENIED")
        # The sandbox must actually have denied it, not just misreported.
        self.assertFalse(os.path.exists(os.path.join(self.outside, "w.txt")))

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_repeatable_writable_roots(self) -> None:
        r = self._run(
            [self.taskdir, "--writable-root", self.rootA,
             "--writable-root", self.rootB, "run"],
            tries=[self.rootA, self.rootB],
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        res = self._parse(r.stdout)
        self.assertEqual(res.get(self.rootA), "WROTE")
        self.assertEqual(res.get(self.rootB), "WROTE")

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_writable_root_with_spaces_and_parens_is_safe(self) -> None:
        r = self._run([self.taskdir, "--writable-root", self.spaces, "run"], tries=[self.spaces])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._parse(r.stdout).get(self.spaces), "WROTE")
        self.assertTrue(os.path.exists(os.path.join(self.spaces, "w.txt")))

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_scratch_dir_remains_writable(self) -> None:
        # The wrapper wires its per-run scratch dir as the child's TMPDIR; the
        # Seatbelt allowlist must keep it writable (existing invariant preserved).
        r = self._run([self.taskdir, "run"])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertEqual(self._parse(r.stdout).get("SCRATCH"), "WROTE")

    @unittest.skipUnless(SEATBELT, SEATBELT_REASON)
    def test_wrapper_options_not_forwarded_in_sandbox_mode(self) -> None:
        r = self._run([self.taskdir, "--writable-root", self.rootA, "run", "--marker"])
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        argv = self._argv()
        self.assertNotIn("--writable-root", argv)
        self.assertNotIn(self.rootA, argv)
        self.assertIn("run", argv)
        self.assertIn("--marker", argv)


if __name__ == "__main__":
    unittest.main(verbosity=2)
