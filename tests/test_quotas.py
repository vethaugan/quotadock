import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from urllib.error import HTTPError

from quotadock.models import Quota, Snapshot, countdown_text, parse_codex, parse_claude, parse_gemini, percent_text, usage_tone
from quotadock.controller import preserve_last_success
from quotadock.providers import NoRedirect, ProviderError, request_json


class QuotaTests(unittest.TestCase):
    def test_codex_shared_weekly_and_no_duplicated_legacy_field(self):
        limit = {"primary": {"usedPercent": 22, "windowDurationMins": 10080, "resetsAt": 1793774283}}
        rows = parse_codex({"rateLimits": limit, "rateLimitsByLimitId": {"codex": limit}})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].remaining_percent, 78)
        self.assertEqual(rows[0].label, "7-day")
        self.assertIsNotNone(rows[0].resets_at.tzinfo)

    def test_codex_handles_null_and_future_named_limits(self):
        self.assertEqual(parse_codex({"rateLimits": None}), ())
        rows = parse_codex({"rateLimitsByLimitId": {"future": {"limitName": "New model", "primary": {"usedPercent": 100}}}})
        self.assertEqual(rows[0].remaining_percent, 0)
        self.assertIn("New model", rows[0].label)

    def test_missing_or_invalid_percent_is_not_zero(self):
        for value in (None, "22", True, -1, 101, float("nan"), float("inf")):
            self.assertEqual(parse_codex({"rateLimits": {"primary": {"usedPercent": value}}}), ())
        self.assertEqual(parse_codex({"rateLimits": {"primary": {"usedPercent": 0}}})[0].remaining_percent, 100)

    def test_claude_dynamic_windows_and_extra_usage_not_mixed(self):
        rows = parse_claude({"five_hour": {"utilization": 10, "resets_at": "2026-11-04T06:38:03Z"}, "future_model": {"utilization": 60}, "extra_usage": {"utilization": 20}})
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].remaining_percent, 90)
        self.assertEqual(rows[1].label, "Future model")

    def test_gemini_fraction_means_remaining_not_used(self):
        rows = parse_gemini({"buckets": [{"modelId": "example", "remainingFraction": 0.73}, {"modelId": "unknown"}, {"remainingFraction": True}, {"remainingFraction": 2}]})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].remaining_percent, 73)
        self.assertEqual(parse_gemini({"buckets": None}), ())

    def test_failed_refresh_preserves_last_reading_as_stale(self):
        old = Snapshot("OpenAI", "Shared plan", (Quota("Weekly", 22),), "Live", fetched_at=datetime.now(timezone.utc))
        failure = Snapshot("OpenAI", "Shared plan", status="Offline")
        result = preserve_last_success(old, failure)
        self.assertTrue(result.stale)
        self.assertEqual(result.quotas, old.quotas)
        self.assertEqual(result.fetched_at, old.fetched_at)
        self.assertEqual(result.status, "Offline")
        fresh = Snapshot("OpenAI", "Shared plan", (), "Not reported", fetched_at=datetime.now(timezone.utc))
        self.assertEqual(preserve_last_success(result, fresh), fresh)

    def test_reset_never_assumes_quota_replenished(self):
        quota = Quota("Weekly", 100, datetime.now(timezone.utc)-timedelta(seconds=1))
        self.assertEqual(quota.remaining_percent, 0)
        self.assertIn("awaiting refresh", quota.reset_text())

    def test_countdown_formats_days_hours_and_seconds(self):
        now = datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(countdown_text(now + timedelta(days=2, hours=14, minutes=5), now), "2d 14h")
        self.assertEqual(countdown_text(now + timedelta(hours=3, minutes=5), now), "3h 05m")
        self.assertEqual(countdown_text(now + timedelta(minutes=48, seconds=9), now), "48m 09s")
        self.assertEqual(countdown_text(now - timedelta(seconds=1), now), "due")
        self.assertEqual(countdown_text(None, now), "—")

    def test_percent_never_rounds_to_empty_or_exhausted(self):
        self.assertEqual(percent_text(99.6), "99")
        self.assertEqual(percent_text(100), "100")
        self.assertEqual(percent_text(0.3), "<1")
        self.assertEqual(percent_text(0), "0")
        self.assertEqual(percent_text(26.6667), "27")

    def test_usage_tone_thresholds(self):
        self.assertEqual([usage_tone(v) for v in (0, 64.9, 65, 84.9, 85, 100)],
                         ["normal", "normal", "warning", "warning", "critical", "critical"])

    def test_headline_is_most_constrained_window(self):
        snapshot = Snapshot("Anthropic", "Claude Code", (Quota("5-hour", 30), Quota("Weekly", 72), Quota("Opus", 10)))
        self.assertEqual(snapshot.headline.label, "Weekly")
        self.assertIsNone(Snapshot("OpenAI", "Shared plan").headline)

    def test_credentials_never_follow_redirects(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example/"))

    @patch("quotadock.providers.urllib.request.build_opener")
    def test_rate_limit_enforces_retry_after(self, opener):
        opener.return_value.open.side_effect = HTTPError("https://example.test", 429, "limit", {"Retry-After": "1800"}, None)
        with self.assertRaises(ProviderError) as error:
            request_json("https://example.test", "test-token")
        self.assertEqual(error.exception.retry_after, 1800)
        self.assertNotIn("test-token", str(error.exception))


if __name__ == "__main__":
    unittest.main()
