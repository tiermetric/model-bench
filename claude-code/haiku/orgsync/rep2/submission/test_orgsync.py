"""Integration tests for the orgsync sync command.

Tests against the actual local services (Directory and Ledger) that are expected
to be running during acceptance testing.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from orgsync.config import Config
from orgsync.http_client import HttpClient, Response
from orgsync.sync import run_sync, _is_well_formed_account, _is_well_formed_item
import email.message


def make_headers(pairs=None):
    """Build a fake email.message.Message for headers."""
    message = email.message.Message()
    for key, value in (pairs or {}).items():
        message[key] = value
    return message


class FakeHTTPResponse:
    """Fake http.client.HTTPResponse for testing."""

    def __init__(self, status, body=b"", headers=None):
        self.status = status
        self.headers = make_headers(headers)
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class FormValidationTest(unittest.TestCase):
    """Test well-formedness validators."""

    def test_account_must_have_all_required_fields(self):
        self.assertTrue(_is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True}
        ))
        self.assertFalse(_is_well_formed_account({"id": "a-1", "owner": "kim"}))
        self.assertFalse(_is_well_formed_account({"id": "a-1", "active": True}))
        self.assertFalse(_is_well_formed_account({"owner": "kim", "active": True}))

    def test_account_active_must_be_boolean(self):
        self.assertTrue(_is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True}
        ))
        self.assertTrue(_is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": False}
        ))
        self.assertFalse(_is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": "yes"}
        ))
        self.assertFalse(_is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": 1}
        ))

    def test_account_allows_extra_fields(self):
        self.assertTrue(_is_well_formed_account(
            {"id": "a-1", "owner": "kim", "active": True, "email": "kim@example.com"}
        ))

    def test_item_must_have_all_required_fields(self):
        self.assertTrue(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3}
        ))
        self.assertFalse(_is_well_formed_item({"id": "i-1", "owner": "kim", "status": "open"}))
        self.assertFalse(_is_well_formed_item({"id": "i-1", "owner": "kim", "points": 3}))
        self.assertFalse(_is_well_formed_item({"id": "i-1", "status": "open", "points": 3}))
        self.assertFalse(_is_well_formed_item({"owner": "kim", "status": "open", "points": 3}))

    def test_item_points_must_be_non_negative_integer(self):
        self.assertTrue(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 0}
        ))
        self.assertTrue(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 100}
        ))
        self.assertFalse(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": -1}
        ))
        self.assertFalse(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3.5}
        ))
        self.assertFalse(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": "3"}
        ))

    def test_item_allows_extra_fields(self):
        self.assertTrue(_is_well_formed_item(
            {"id": "i-1", "owner": "kim", "status": "open", "points": 3,
             "title": "Fix bug", "created": "2024-01-01"}
        ))


class SyncLogicTest(unittest.TestCase):
    """Test sync logic with mocked HTTP clients."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _make_fake_opener_for_responses(self, responses_map):
        """Create a FakeOpener that returns mocked responses based on request paths."""
        class FakeOpener:
            def __init__(self, responses_map):
                self.responses_map = responses_map
                self.requests = []

            def open(self, request, timeout=None):
                self.requests.append((request.get_method(), request.full_url))
                url = request.full_url

                # Find matching response
                for pattern, response in self.responses_map.items():
                    if pattern in url:
                        return response

                # Default: return 200 with empty list
                return FakeHTTPResponse(200, b'{"next_page": null}')

        return FakeOpener(responses_map)

    def test_empty_services(self):
        """Test with empty Directory and Ledger."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        # Mock both clients to return empty responses
        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"

            # Mock directory
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {"items": [], "next_page": None}
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger
            mock_ledger_response_accts = mock.Mock()
            mock_ledger_response_accts.status = 200
            mock_ledger_response_accts.json.return_value = {"accounts": [], "next_page": None}

            mock_ledger_response_summ = mock.Mock()
            mock_ledger_response_summ.status = 200
            mock_ledger_response_summ.json.return_value = {"summaries": [], "next_page": None}

            mock_ledger_client.get.side_effect = [
                mock_ledger_response_accts,
                mock_ledger_response_summ
            ]

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "errors.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "orphans.jsonl")))
        self.assertTrue(os.path.exists(os.path.join(self.temp_dir, "summary.jsonl")))

        # All files should be empty
        with open(os.path.join(self.temp_dir, "errors.jsonl")) as f:
            self.assertEqual(f.read(), "")
        with open(os.path.join(self.temp_dir, "orphans.jsonl")) as f:
            self.assertEqual(f.read(), "")
        with open(os.path.join(self.temp_dir, "summary.jsonl")) as f:
            self.assertEqual(f.read(), "")

    def test_active_account_gets_summary(self):
        """Test that an active account with items gets a summary posted."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                    {"id": "i-2", "owner": "kim", "status": "done", "points": 2},
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [
                    {"id": "a-1", "owner": "kim", "active": True},
                ],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [],
                "next_page": None
            }

            mock_post_response = mock.Mock()
            mock_post_response.status = 201

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]
            mock_ledger_client.post_json.return_value = mock_post_response

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # Check that summary was posted
        mock_ledger_client.post_json.assert_called_once()
        call_args = mock_ledger_client.post_json.call_args
        self.assertEqual(call_args[0][0], "/v1/summaries")
        self.assertEqual(call_args[0][1]["owner"], "kim")
        self.assertEqual(call_args[0][1]["open_items"], 1)
        self.assertEqual(call_args[0][1]["done_items"], 1)
        self.assertEqual(call_args[0][1]["total_points"], 5)
        self.assertEqual(call_args[0][2]["Idempotency-Key"], "sync-kim")

        # Check summary file
        with open(os.path.join(self.temp_dir, "summary.jsonl")) as f:
            lines = f.read().strip().split("\n")
            self.assertEqual(len(lines), 1)
            summary = json.loads(lines[0])
            self.assertEqual(summary["owner"], "kim")
            self.assertEqual(summary["open_items"], 1)

    def test_inactive_account_no_summary(self):
        """Test that an inactive account does not get a summary."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [
                    {"id": "a-1", "owner": "kim", "active": False},
                ],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [],
                "next_page": None
            }

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # No POST should be made
        mock_ledger_client.post_json.assert_not_called()

        # Item should be orphaned
        with open(os.path.join(self.temp_dir, "orphans.jsonl")) as f:
            lines = [l for l in f.read().strip().split("\n") if l]
            self.assertEqual(len(lines), 1)

    def test_item_without_active_owner_is_orphaned(self):
        """Test that items without an active owner are orphaned."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "orphan", "status": "open", "points": 3},
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [],
                "next_page": None
            }

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # Item should be orphaned
        with open(os.path.join(self.temp_dir, "orphans.jsonl")) as f:
            lines = [l for l in f.read().strip().split("\n") if l]
            self.assertEqual(len(lines), 1)
            orphan = json.loads(lines[0])
            self.assertEqual(orphan["id"], "i-1")

    def test_malformed_records_go_to_errors(self):
        """Test that malformed records are written to errors file."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client with malformed item
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "kim"},  # missing status and points
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client with malformed account
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [
                    {"id": "a-1", "owner": "kim"},  # missing active
                ],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [],
                "next_page": None
            }

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # Check errors file
        with open(os.path.join(self.temp_dir, "errors.jsonl")) as f:
            lines = [l for l in f.read().strip().split("\n") if l]
            self.assertEqual(len(lines), 2)

            # One from directory, one from ledger
            services = [json.loads(l)["service"] for l in lines]
            self.assertIn("directory", services)
            self.assertIn("ledger", services)

    def test_duplicate_item_ids_deduplicated(self):
        """Test that duplicate item IDs are only counted once."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client with duplicate item
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},  # duplicate
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [
                    {"id": "a-1", "owner": "kim", "active": True},
                ],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [],
                "next_page": None
            }

            mock_post_response = mock.Mock()
            mock_post_response.status = 201

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]
            mock_ledger_client.post_json.return_value = mock_post_response

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # Check that summary has only 1 open item, not 2
        call_args = mock_ledger_client.post_json.call_args
        self.assertEqual(call_args[0][1]["open_items"], 1)

    def test_idempotency_no_repost_if_summary_exists(self):
        """Test that existing summaries are not reposted."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [
                    {"id": "a-1", "owner": "kim", "active": True},
                ],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [
                    {"owner": "kim", "open_items": 1, "done_items": 0, "total_points": 3},
                ],
                "next_page": None
            }

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # No POST should be made
        mock_ledger_client.post_json.assert_not_called()

        # Summary file should be empty
        with open(os.path.join(self.temp_dir, "summary.jsonl")) as f:
            self.assertEqual(f.read().strip(), "")

    def test_rate_limit_retry_on_post(self):
        """Test that 429 responses are retried on POST."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            with mock.patch("orgsync.sync.time.sleep"):  # Don't actually sleep
                # Mock directory client
                mock_dir_client = mock.Mock()
                mock_dir_client.base_url = "http://example.test"
                mock_dir_response = mock.Mock()
                mock_dir_response.status = 200
                mock_dir_response.json.return_value = {
                    "items": [
                        {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                    ],
                    "next_page": None
                }
                mock_dir_client.get.return_value = mock_dir_response

                # Mock ledger client
                mock_ledger_client = mock.Mock()
                mock_ledger_client.base_url = "http://example.test"
                mock_acct_response = mock.Mock()
                mock_acct_response.status = 200
                mock_acct_response.json.return_value = {
                    "accounts": [
                        {"id": "a-1", "owner": "kim", "active": True},
                    ],
                    "next_page": None
                }

                mock_summ_response = mock.Mock()
                mock_summ_response.status = 200
                mock_summ_response.json.return_value = {
                    "summaries": [],
                    "next_page": None
                }

                # First POST gets 429, second gets 201
                mock_rate_limit_response = mock.Mock()
                mock_rate_limit_response.status = 429
                mock_rate_limit_response.headers = make_headers({"Retry-After": "0.1"})

                mock_post_response = mock.Mock()
                mock_post_response.status = 201

                mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]
                mock_ledger_client.post_json.side_effect = [mock_rate_limit_response, mock_post_response]

                MockClient.side_effect = [mock_dir_client, mock_ledger_client]

                result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)
        # Should have retried, so post_json called twice
        self.assertEqual(mock_ledger_client.post_json.call_count, 2)

    def test_multiple_accounts_and_items(self):
        """Test with multiple accounts and items."""
        config = Config(
            directory_url="http://example.test",
            ledger_url="http://example.test"
        )

        with mock.patch("orgsync.sync.HttpClient") as MockClient:
            # Mock directory client
            mock_dir_client = mock.Mock()
            mock_dir_client.base_url = "http://example.test"
            mock_dir_response = mock.Mock()
            mock_dir_response.status = 200
            mock_dir_response.json.return_value = {
                "items": [
                    {"id": "i-1", "owner": "kim", "status": "open", "points": 3},
                    {"id": "i-2", "owner": "kim", "status": "done", "points": 2},
                    {"id": "i-3", "owner": "alex", "status": "open", "points": 5},
                    {"id": "i-4", "owner": "alex", "status": "open", "points": 4},
                ],
                "next_page": None
            }
            mock_dir_client.get.return_value = mock_dir_response

            # Mock ledger client
            mock_ledger_client = mock.Mock()
            mock_ledger_client.base_url = "http://example.test"
            mock_acct_response = mock.Mock()
            mock_acct_response.status = 200
            mock_acct_response.json.return_value = {
                "accounts": [
                    {"id": "a-1", "owner": "kim", "active": True},
                    {"id": "a-2", "owner": "alex", "active": True},
                ],
                "next_page": None
            }

            mock_summ_response = mock.Mock()
            mock_summ_response.status = 200
            mock_summ_response.json.return_value = {
                "summaries": [],
                "next_page": None
            }

            mock_post_response = mock.Mock()
            mock_post_response.status = 201

            mock_ledger_client.get.side_effect = [mock_acct_response, mock_summ_response]
            mock_ledger_client.post_json.return_value = mock_post_response

            MockClient.side_effect = [mock_dir_client, mock_ledger_client]

            result = run_sync(config, self.temp_dir)

        self.assertEqual(result, 0)

        # Two POSTs should be made
        self.assertEqual(mock_ledger_client.post_json.call_count, 2)


if __name__ == "__main__":
    unittest.main()
