"""CDP 자동 로그인 회귀 테스트."""

import os
import unittest
from unittest.mock import patch

from src.crawlers.auth import cdp


class CDPAutofillTests(unittest.TestCase):
    def test_load_login_credentials_from_env_returns_none_without_complete_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(cdp.load_login_credentials_from_env())

        with patch.dict(os.environ, {"SKIM_LOGIN_IDENTIFIER": "user@example.com"}, clear=True):
            self.assertIsNone(cdp.load_login_credentials_from_env())

    def test_load_login_credentials_from_env_returns_identifier_and_password(self):
        with patch.dict(
            os.environ,
            {
                "SKIM_LOGIN_IDENTIFIER": "user@example.com",
                "SKIM_LOGIN_PASSWORD": "super-secret",
            },
            clear=True,
        ):
            self.assertEqual(
                cdp.load_login_credentials_from_env(),
                ("user@example.com", "super-secret"),
            )

    @patch("src.crawlers.auth.cdp.execute_cdp_command")
    def test_attempt_login_autofill_skips_when_credentials_are_missing(self, execute_cdp_command):
        result = cdp.attempt_login_autofill("ws://example", "threads", None, None)

        self.assertEqual(
            result,
            {
                "attempted": False,
                "identifierFilled": False,
                "passwordFilled": False,
                "actionClicked": False,
            },
        )
        execute_cdp_command.assert_not_called()

    @patch("src.crawlers.auth.cdp.execute_cdp_command")
    def test_attempt_login_autofill_runs_runtime_evaluate_with_platform_script(
        self, execute_cdp_command
    ):
        execute_cdp_command.return_value = {
            "result": {
                "value": {
                    "identifierFilled": True,
                    "passwordFilled": True,
                    "actionClicked": True,
                }
            }
        }

        result = cdp.attempt_login_autofill(
            "ws://example",
            "x",
            "user@example.com",
            "super-secret",
        )

        self.assertEqual(
            result,
            {
                "attempted": True,
                "identifierFilled": True,
                "passwordFilled": True,
                "actionClicked": True,
            },
        )
        execute_cdp_command.assert_called_once()
        self.assertEqual(execute_cdp_command.call_args.args[1], "Runtime.evaluate")

        expression = execute_cdp_command.call_args.args[2]["expression"]
        self.assertIn('input[autocomplete=\\"username\\"]', expression)
        self.assertIn('input[name=\\"password\\"]', expression)
        self.assertIn("requestSubmit", expression)
        self.assertIn("KeyboardEvent", expression)
        self.assertIn("super-secret", expression)
        self.assertTrue(execute_cdp_command.call_args.args[2]["awaitPromise"])

    @patch("src.crawlers.auth.cdp.execute_cdp_command")
    def test_attempt_login_autofill_uses_reddit_selectors(self, execute_cdp_command):
        execute_cdp_command.return_value = {
            "result": {
                "value": {
                    "identifierFilled": True,
                    "passwordFilled": True,
                    "actionClicked": False,
                }
            }
        }

        result = cdp.attempt_login_autofill(
            "ws://example",
            "reddit",
            "user@example.com",
            "super-secret",
        )

        self.assertEqual(
            result,
            {
                "attempted": True,
                "identifierFilled": True,
                "passwordFilled": True,
                "actionClicked": False,
            },
        )

        expression = execute_cdp_command.call_args.args[2]["expression"]
        self.assertIn('input[name=\\"username\\"]', expression)
        self.assertIn('input[name=\\"password\\"]', expression)
        self.assertIn('button[type=\\"submit\\"]', expression)


if __name__ == "__main__":
    unittest.main()
