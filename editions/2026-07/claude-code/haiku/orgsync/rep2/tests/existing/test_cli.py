"""Tests for the command-line surface in orgsync.__main__.

These pin the CLI contract: which commands exist, what they are called, what
options they take, and how a configuration problem is reported. They exercise
argument handling only — none of them runs a reconciliation.
"""

import contextlib
import io
import json
import os
import tempfile
import unittest

from orgsync import __version__
from orgsync.__main__ import EXIT_CONFIG_ERROR, build_parser, main


def run_main(argv):
    """Run `main(argv)`, returning (exit_code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    return code, out.getvalue(), err.getvalue()


class ParserTest(unittest.TestCase):
    def setUp(self):
        self.parser = build_parser()

    def test_known_commands(self):
        for command in ("version", "show-config", "sync"):
            with self.subTest(command=command):
                self.assertEqual(self.parser.parse_args([command]).command, command)

    def test_sync_option_defaults_and_overrides(self):
        args = self.parser.parse_args(["sync"])
        self.assertEqual((args.config, args.out_dir), ("config.json", "out"))
        args = self.parser.parse_args(["sync", "--config", "o.json", "--out-dir", "d"])
        self.assertEqual((args.config, args.out_dir), ("o.json", "d"))

    def test_missing_or_unknown_command_is_a_usage_error(self):
        for argv in ([], ["frobnicate"]):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    self.parser.parse_args(argv)
                self.assertEqual(caught.exception.code, 2)


class VersionCommandTest(unittest.TestCase):
    def test_prints_the_package_version_and_exits_zero(self):
        code, out, _ = run_main(["version"])
        self.assertEqual(code, 0)
        self.assertIn(__version__, out)


class ShowConfigCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "config.json")
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump({"directory_url": "http://127.0.0.1:1",
                       "ledger_url": "http://127.0.0.1:2"}, handle)

    def test_prints_both_urls(self):
        code, out, _ = run_main(["show-config", "--config", self.path])
        self.assertEqual(code, 0)
        self.assertIn("http://127.0.0.1:1", out)
        self.assertIn("http://127.0.0.1:2", out)

    def test_missing_config_is_reported_not_raised(self):
        missing = os.path.join(self.tmp.name, "absent.json")
        code, _, err = run_main(["show-config", "--config", missing])
        self.assertEqual(code, EXIT_CONFIG_ERROR)
        self.assertIn("absent.json", err)


if __name__ == "__main__":
    unittest.main()
