"""CLI help stays aligned with registered platforms."""

import unittest

from typer.testing import CliRunner

from skim_cli.cli import app
from skim_core.crawlers import REGISTRY


class CliHelpTests(unittest.TestCase):
    def setUp(self):
        self.runner = CliRunner()

    def test_root_help_reports_current_platform_count(self):
        result = self.runner.invoke(app, ["--help"])

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn(f"{len(REGISTRY)}개 플랫폼 지원", result.stdout)
        self.assertNotIn("11개 플랫폼 지원", result.stdout)

    def test_crawl_help_lists_registered_platforms(self):
        result = self.runner.invoke(app, ["crawl", "--help"])

        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("all", result.stdout)
        for platform in REGISTRY:
            self.assertIn(platform, result.stdout)

    def test_unknown_platform_error_lists_registered_platforms(self):
        result = self.runner.invoke(app, ["crawl", "unknown-platform"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("알 수 없는 플랫폼: unknown-platform", result.stdout)
        self.assertIn("지원 플랫폼:", result.stdout)
        for platform in REGISTRY:
            self.assertIn(platform, result.stdout)


if __name__ == "__main__":
    unittest.main()
