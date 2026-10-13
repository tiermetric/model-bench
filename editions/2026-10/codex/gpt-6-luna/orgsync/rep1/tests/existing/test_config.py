"""Tests for orgsync.config — loading and validating config.json."""

import json
import os
import tempfile
import unittest

from orgsync.config import Config, ConfigError, load_config


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, payload, name="config.json"):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(payload if isinstance(payload, str) else json.dumps(payload))
        return path

    def test_loads_both_urls(self):
        path = self.write({"directory_url": "http://127.0.0.1:1",
                           "ledger_url": "http://127.0.0.1:2"})
        self.assertEqual(load_config(path),
                         Config("http://127.0.0.1:1", "http://127.0.0.1:2"))

    def test_whitespace_trailing_slash_stripped_and_extra_keys_ignored(self):
        path = self.write({"directory_url": " http://127.0.0.1:1/ ",
                           "ledger_url": "http://127.0.0.1:2", "note": "hello"})
        config = load_config(path)
        self.assertEqual(config.directory_url, "http://127.0.0.1:1")
        self.assertEqual(config.ledger_url, "http://127.0.0.1:2")

    def test_missing_file_raises_config_error(self):
        with self.assertRaises(ConfigError):
            load_config(os.path.join(self.tmp.name, "absent.json"))

    def test_missing_key_raises_config_error_naming_the_key(self):
        path = self.write({"directory_url": "http://127.0.0.1:1"})
        with self.assertRaises(ConfigError) as caught:
            load_config(path)
        self.assertIn("ledger_url", str(caught.exception))

    def test_empty_value_raises_config_error(self):
        path = self.write({"directory_url": "  ", "ledger_url": "http://127.0.0.1:2"})
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_invalid_or_non_object_json_raises_config_error(self):
        with self.assertRaises(ConfigError):
            load_config(self.write("{not json", "a.json"))
        with self.assertRaises(ConfigError):
            load_config(self.write([1, 2, 3], "b.json"))


if __name__ == "__main__":
    unittest.main()
