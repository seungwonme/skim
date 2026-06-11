"""run 추적 및 YouTube fallback 회귀 테스트."""

import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skim_core.db import get_connection, init_db, save_posts, save_run, update_run_progress
from skim_core.enrichment import _select_youtube_subtitle_languages, enrich_with_content
from skim_core.models import Post


class RunTrackingTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "skim.db"
        init_db(self.db_path)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_save_run_cleans_stale_running_rows_and_records_runner_metadata(self):
        conn = get_connection(self.db_path)
        conn.execute(
            """
            INSERT INTO runs (status, current_platform, runner_pid, runner_host)
            VALUES ('running', 'producthunt', 424242, ?)
            """,
            (socket.gethostname(),),
        )
        conn.commit()
        conn.close()

        with patch("skim_core.db._pid_is_alive", return_value=False):
            new_run_id = save_run(db_path=self.db_path)

        conn = get_connection(self.db_path)
        stale_run = conn.execute(
            "SELECT status, current_platform, summary FROM runs WHERE id = 1"
        ).fetchone()
        new_run = conn.execute(
            "SELECT status, runner_pid, runner_host FROM runs WHERE id = ?",
            (new_run_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(stale_run["status"], "interrupted")
        self.assertEqual(stale_run["current_platform"], "producthunt")
        self.assertIn("producthunt", stale_run["summary"])
        self.assertEqual(new_run["status"], "running")
        self.assertIsNotNone(new_run["runner_pid"])
        self.assertEqual(new_run["runner_host"], socket.gethostname())

    def test_update_run_progress_persists_current_platform_and_summary(self):
        run_id = save_run(db_path=self.db_path)

        update_run_progress(
            run_id,
            "arxiv",
            "arxiv 크롤링 시작",
            db_path=self.db_path,
        )

        conn = get_connection(self.db_path)
        run = conn.execute(
            "SELECT current_platform, summary FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        conn.close()

        self.assertEqual(run["current_platform"], "arxiv")
        self.assertEqual(run["summary"], "arxiv 크롤링 시작")

    def test_save_posts_backfills_missing_content_for_existing_row(self):
        initial = Post(
            platform="youtube",
            author="tester",
            content="video title",
            timestamp="2026-04-09T00:00:00+09:00",
            title="Video",
            summary="짧은 요약",
            external_id="video-1",
            content_markdown="",
            word_count=0,
        )
        enriched = Post(
            platform="youtube",
            author="tester",
            content="video title",
            timestamp="2026-04-09T00:00:00+09:00",
            title="Video",
            summary="짧은 요약",
            external_id="video-1",
            content_markdown="full transcript text",
            word_count=3,
        )

        saved_initial = save_posts([initial], "youtube", db_path=self.db_path)
        saved_enriched = save_posts([enriched], "youtube", db_path=self.db_path)

        conn = get_connection(self.db_path)
        row = conn.execute("""
            SELECT content_markdown, word_count
            FROM posts
            WHERE platform = 'youtube' AND external_id = 'video-1'
            """).fetchone()
        conn.close()

        self.assertEqual(saved_initial, 1)
        self.assertEqual(saved_enriched, 1)
        self.assertEqual(row["content_markdown"], "full transcript text")
        self.assertEqual(row["word_count"], 3)

    def test_save_posts_replaces_summary_fallback_when_transcript_arrives(self):
        fallback = {
            "platform": "youtube",
            "author": "tester",
            "content": "video title",
            "timestamp": "2026-04-09T00:00:00+09:00",
            "title": "Video",
            "summary": "fallback summary",
            "external_id": "video-2",
            "content_markdown": "fallback summary",
            "word_count": 2,
            "subtitle_lang": "summary",
        }
        transcript = {
            "platform": "youtube",
            "author": "tester",
            "content": "video title",
            "timestamp": "2026-04-09T00:00:00+09:00",
            "title": "Video",
            "summary": "fallback summary",
            "external_id": "video-2",
            "content_markdown": "actual transcript text",
            "word_count": 3,
            "subtitle_lang": "ko",
        }

        saved_fallback = save_posts([fallback], "youtube", db_path=self.db_path)
        saved_transcript = save_posts([transcript], "youtube", db_path=self.db_path)

        conn = get_connection(self.db_path)
        row = conn.execute("""
            SELECT content_markdown, word_count, extra
            FROM posts
            WHERE platform = 'youtube' AND external_id = 'video-2'
            """).fetchone()
        conn.close()

        self.assertEqual(saved_fallback, 1)
        self.assertEqual(saved_transcript, 1)
        self.assertEqual(row["content_markdown"], "actual transcript text")
        self.assertEqual(row["word_count"], 3)
        self.assertIn('"subtitle_lang": "ko"', row["extra"])


class YouTubeFallbackTests(unittest.TestCase):
    def test_select_youtube_subtitle_languages_prefers_exact_codes(self):
        info = """
[info] Available automatic captions for test:
Language                    Name                    Formats
en-zh-Hans-example          English from Chinese    vtt, srt
ko-zh-Hans-example          Korean from Chinese     vtt, srt
[info] Available subtitles for test:
Language                    Name                    Formats
en-US-y-JJSUA13BM           English (United States) vtt, srt
ko-0L2zzeR32C4              Korean                  vtt, srt
"""

        selected = _select_youtube_subtitle_languages(info)

        self.assertEqual(selected, "en-US-y-JJSUA13BM,ko-0L2zzeR32C4")

    @patch("skim_core.enrichment.extract_youtube_transcript", return_value=None)
    def test_enrich_with_content_uses_summary_when_youtube_transcript_is_missing(self, _extract):
        item = {
            "platform": "youtube/Test Channel",
            "title": "Fallback video",
            "url": "https://www.youtube.com/watch?v=example",
            "summary": "이 영상은 에이전트 루프와 도구 사용 패턴을 설명한다.",
        }

        enrich_with_content([item])

        self.assertEqual(item["content_markdown"], item["summary"])
        self.assertGreater(item["word_count"], 0)
        self.assertEqual(item["subtitle_lang"], "summary")


if __name__ == "__main__":
    unittest.main()
