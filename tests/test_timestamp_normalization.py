"""Phase 0 timestamp normalization 회귀 테스트."""

import unittest
from datetime import datetime, timedelta, timezone

from skim_core.timestamp import _REL_KO, UTC, epoch_to_iso, relative_ko_to_iso, to_utc_iso

KST = timezone(timedelta(hours=9))


class EpochToIsoTests(unittest.TestCase):
    def test_epoch_to_iso_10_digit_seconds(self):
        # 1700000000 → 2023-11-14T22:13:20+00:00
        self.assertEqual(epoch_to_iso(1700000000), "2023-11-14T22:13:20+00:00")

    def test_epoch_to_iso_13_digit_ms_divides_1000(self):
        # 1712345678901 (ms) ÷ 1000 → 1712345678 sec
        self.assertEqual(epoch_to_iso(1712345678901), "2024-04-05T19:34:38+00:00")

    def test_epoch_to_iso_16_digit_micro_divides_1m(self):
        # 1712345678901234 (micro) ÷ 1_000_000 → 1712345678 sec
        self.assertEqual(epoch_to_iso(1712345678901234), "2024-04-05T19:34:38+00:00")

    def test_epoch_to_iso_accepts_string(self):
        self.assertEqual(epoch_to_iso("1700000000"), "2023-11-14T22:13:20+00:00")

    def test_epoch_to_iso_zero_returns_unix_epoch(self):
        self.assertEqual(epoch_to_iso(0), "1970-01-01T00:00:00+00:00")

    def test_epoch_to_iso_12_digit_ms_divides_1000(self):
        """codex review MEDIUM: 12-digit ms 도 ms 로 인식해야 함."""
        # 999_999_999_999 ms = 2001-09-09T01:46:39+00:00
        result = epoch_to_iso(999_999_999_999)
        self.assertTrue(result.startswith("2001-"), f"got {result}")


class RelKoPatternTests(unittest.TestCase):
    def test_rel_ko_detects_three_hours_ago(self):
        m = _REL_KO.search("3시간 전")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "3시간 전")

    def test_rel_ko_multiunit_detected(self):
        m = _REL_KO.search("1시간 30분 전")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "1시간 30분 전")

    def test_rel_ko_detects_without_space(self):
        m = _REL_KO.search("3시간전")
        self.assertIsNotNone(m)
        self.assertEqual(m.group(0), "3시간전")

    def test_rel_ko_no_match_when_no_jeon(self):
        self.assertIsNone(_REL_KO.search("3시간"))


class RelativeKoToIsoTests(unittest.TestCase):
    NOW = datetime(2026, 4, 19, 12, 0, 0, tzinfo=UTC)

    def test_relative_ko_single_unit_minutes(self):
        result = relative_ko_to_iso("10분 전", now=self.NOW)
        expected = (self.NOW - timedelta(minutes=10)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_single_unit_hours(self):
        result = relative_ko_to_iso("3시간 전", now=self.NOW)
        expected = (self.NOW - timedelta(hours=3)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_single_unit_days(self):
        result = relative_ko_to_iso("5일 전", now=self.NOW)
        expected = (self.NOW - timedelta(days=5)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_single_unit_weeks(self):
        result = relative_ko_to_iso("2주 전", now=self.NOW)
        expected = (self.NOW - timedelta(weeks=2)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_single_unit_months_dal(self):
        result = relative_ko_to_iso("3달 전", now=self.NOW)
        expected = (self.NOW - timedelta(days=90)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_single_unit_months_gaewol(self):
        result = relative_ko_to_iso("3개월 전", now=self.NOW)
        expected = (self.NOW - timedelta(days=90)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_single_unit_years(self):
        result = relative_ko_to_iso("2년 전", now=self.NOW)
        expected = (self.NOW - timedelta(days=730)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_multi_unit_sums(self):
        result = relative_ko_to_iso("1시간 30분 전", now=self.NOW)
        expected = (self.NOW - timedelta(hours=1, minutes=30)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_matches_prefix_before_suffix_text(self):
        """9차 P3-7: '3시간 전 작성' 같이 뒤 텍스트가 붙어도 prefix 만 매칭."""
        result = relative_ko_to_iso("3시간 전 작성", now=self.NOW)
        expected = (self.NOW - timedelta(hours=3)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_without_jeon_returns_none(self):
        self.assertIsNone(relative_ko_to_iso("3시간", now=self.NOW))

    def test_relative_ko_unparseable_returns_none(self):
        self.assertIsNone(relative_ko_to_iso("어제 작성됨", now=self.NOW))

    def test_relative_ko_ignores_pairs_outside_match_span(self):
        """codex review MEDIUM: '3시간 전 2분' 의 '2분' 은 매칭 span 밖이라 무시."""
        result = relative_ko_to_iso("3시간 전 2분", now=self.NOW)
        expected = (self.NOW - timedelta(hours=3)).isoformat()
        self.assertEqual(result, expected)

    def test_relative_ko_empty_returns_none(self):
        self.assertIsNone(relative_ko_to_iso("", now=self.NOW))

    def test_relative_ko_output_is_utc(self):
        result = relative_ko_to_iso("1시간 전", now=self.NOW)
        self.assertIsNotNone(result)
        dt = datetime.fromisoformat(result)
        self.assertEqual(dt.utcoffset(), timedelta(0))


class ToUtcIsoTests(unittest.TestCase):
    def test_to_utc_iso_already_utc(self):
        result = to_utc_iso("2026-04-19T05:00:00+00:00")
        self.assertEqual(result, "2026-04-19T05:00:00+00:00")

    def test_to_utc_iso_kst_offset_converted(self):
        result = to_utc_iso("2026-04-19T14:00:00+09:00")
        self.assertEqual(result, "2026-04-19T05:00:00+00:00")

    def test_to_utc_iso_naive_treated_as_kst(self):
        # naive → KST → UTC = -9h
        result = to_utc_iso("2026-04-19T14:00:00")
        self.assertEqual(result, "2026-04-19T05:00:00+00:00")

    def test_to_utc_iso_empty_returns_none(self):
        self.assertIsNone(to_utc_iso(""))

    def test_to_utc_iso_invalid_returns_none(self):
        self.assertIsNone(to_utc_iso("not-a-date"))


if __name__ == "__main__":
    unittest.main()
