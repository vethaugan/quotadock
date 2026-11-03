import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from quotadock.models import parse_antigravity
from quotadock.providers import ProviderError, fetch_google, read_antigravity_token


class AntigravityParsingTests(unittest.TestCase):
    def test_weekly_only_plan_does_not_invent_session_limits(self):
        data = {"groups": [
            {"displayName": "Gemini Models", "buckets": [{"bucketId": "gemini-weekly", "window": "weekly", "remainingFraction": 1, "resetTime": "2030-01-08T06:52:30Z"}]},
            {"displayName": "Claude and GPT models", "buckets": [{"bucketId": "3p-weekly", "window": "weekly", "remainingFraction": 0.4}]},
        ]}
        rows = parse_antigravity(data)
        self.assertEqual([q.label for q in rows], ["Gemini · weekly", "Claude & GPT · weekly"])
        self.assertEqual([q.remaining_percent for q in rows], [100, 40])
        self.assertIsNotNone(rows[0].resets_at)

    def test_shared_pools_are_deduplicated_and_new_pools_preserved(self):
        pool = {"bucketId": "gemini-weekly", "remainingFraction": 0.7}
        rows = parse_antigravity({"response": {"groups": [
            {"buckets": [pool]}, {"buckets": [pool]},
            {"displayName": "Future model", "buckets": [{"bucketId": "new-pool", "window": "weekly", "remainingFraction": 0}]},
        ]}})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1].label, "Future model · weekly")
        self.assertEqual(rows[1].remaining_percent, 0)

    def test_missing_invalid_and_disabled_are_not_available_quota(self):
        for value in (None, True, "0.7", -1, 2, float("nan"), float("inf")):
            data = {"groups": [{"buckets": [{"bucketId": "a", "remainingFraction": value}]}]}
            self.assertEqual(parse_antigravity(data), ())
        for disabled in (True, "false", 0):
            data = {"groups": [{"buckets": [{"bucketId": "a", "remainingFraction": 1, "disabled": disabled}]}]}
            self.assertEqual(parse_antigravity(data), ())
        for data in ({}, {"groups": None}, {"response": None}, {"groups": [None, {"buckets": None}]}):
            self.assertEqual(parse_antigravity(data), ())


class AntigravityConnectionTests(unittest.TestCase):
    @patch("quotadock.providers.subprocess.run")
    @patch("quotadock.providers.shutil.which", return_value="/usr/bin/secret-tool")
    def test_reads_only_target_keyring_item(self, which, run):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({"auth_method": "consumer", "token": {"access_token": "fixture-token", "expiry": "2099-01-01T00:00:00Z"}}).encode(), b"")
        self.assertEqual(read_antigravity_token(), "fixture-token")
        self.assertEqual(run.call_args.args[0], ["/usr/bin/secret-tool", "lookup", "service", "gemini", "username", "antigravity"])
        self.assertNotIn("fixture-token", str(run.call_args))
        self.assertEqual(run.call_args.kwargs["timeout"], 12)

    @patch("quotadock.providers.subprocess.run")
    @patch("quotadock.providers.shutil.which", return_value="secret-tool")
    def test_expiry_and_malformed_secrets_have_safe_errors(self, which, run):
        for content in (b"fixture-secret", json.dumps({"token": {"access_token": "fixture-secret", "expiry": "2000-01-01T00:00:00Z"}}).encode()):
            run.return_value = subprocess.CompletedProcess([], 0, content, b"")
            with self.assertRaises(ProviderError) as raised:
                read_antigravity_token()
            self.assertEqual(raised.exception.status, "Sign-in needed")
            self.assertNotIn("fixture-secret", str(raised.exception))

    @patch("quotadock.providers.subprocess.run")
    @patch("quotadock.providers.shutil.which", return_value="secret-tool")
    def test_locked_or_unreachable_wallet_does_not_look_like_missing_account(self, which, run):
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"private diagnostic")
        with self.assertRaises(ProviderError) as raised:
            read_antigravity_token()
        self.assertEqual(raised.exception.status, "Keyring unavailable")
        self.assertNotIn("private diagnostic", str(raised.exception))
        run.side_effect = subprocess.TimeoutExpired("secret-tool", 12)
        with self.assertRaises(ProviderError):
            read_antigravity_token()

    @patch("quotadock.providers.subprocess.run")
    @patch("quotadock.providers.shutil.which", return_value="secret-tool")
    def test_no_item_can_fall_back(self, which, run):
        run.return_value = subprocess.CompletedProcess([], 1, b"", b"")
        self.assertIsNone(read_antigravity_token())

    @patch("quotadock.providers.read_antigravity_token", return_value="fixture-token")
    @patch("quotadock.providers.request_json")
    def test_google_routes_antigravity_to_summary_with_its_own_project(self, request, read):
        request.side_effect = [{"cloudaicompanionProject": "fixture-project"}, {"groups": [{"buckets": [{"bucketId": "gemini-weekly", "remainingFraction": 0.9}]}]}]
        result = fetch_google()
        self.assertEqual(result.product, "Antigravity CLI")
        self.assertEqual(result.quotas[0].remaining_percent, 90)
        self.assertTrue(request.call_args_list[0].args[0].endswith(":loadCodeAssist"))
        self.assertTrue(request.call_args_list[1].args[0].endswith(":retrieveUserQuotaSummary"))
        self.assertEqual(request.call_args_list[1].args[2], {"project": "fixture-project"})
        self.assertIn("QuotaDock/", request.call_args_list[1].args[3]["User-Agent"])

    @patch("quotadock.providers.read_antigravity_token", return_value=None)
    @patch("quotadock.providers.fetch_gemini", return_value="legacy-result")
    def test_legacy_gemini_login_is_retained(self, gemini, read):
        with tempfile.TemporaryDirectory() as directory, patch("quotadock.providers.Path.home", return_value=Path(directory)):
            path = Path(directory)/".gemini/oauth_creds.json"
            path.parent.mkdir()
            path.write_text("{}")
            self.assertEqual(fetch_google(), "legacy-result")
            gemini.assert_called_once()

    @patch("quotadock.providers.read_antigravity_token", side_effect=ProviderError("Sign-in needed", "expired"))
    @patch("quotadock.providers.fetch_gemini")
    def test_expired_antigravity_does_not_silently_switch_accounts(self, gemini, read):
        with self.assertRaises(ProviderError):
            fetch_google()
        gemini.assert_not_called()

    @patch("quotadock.providers.read_antigravity_token", return_value="fixture-token")
    @patch("quotadock.providers.request_json", return_value={"allowedTiers": []})
    def test_missing_project_does_not_query_arbitrary_environment_project(self, request, read):
        with self.assertRaises(ProviderError):
            fetch_google()
        request.assert_called_once()
