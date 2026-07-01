"""CDP 자동 로그인 회귀 테스트."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skim_core.crawlers.auth import cdp


class CDPAutofillTests(unittest.TestCase):
    def test_resolve_login_credentials_returns_explicit_values(self):
        self.assertEqual(
            cdp.resolve_login_credentials(
                "threads",
                login_identifier="user@example.com",
                password="super-secret",
            ),
            ("user@example.com", "super-secret"),
        )

    def test_resolve_login_credentials_requires_identifier_with_password(self):
        with self.assertRaises(ValueError):
            cdp.resolve_login_credentials("threads", password="super-secret")

    def test_save_and_load_login_credentials_uses_keychain_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "skim.db"
            with patch("skim_core.crawlers.auth.cdp.write_keychain_password") as write_password:
                cdp.save_login_credentials_to_keychain(
                    "threads",
                    "user@example.com",
                    "super-secret",
                    db_path=db_path,
                )

            write_password.assert_called_once_with(
                "skim.desktop.threads",
                "user@example.com",
                "super-secret",
            )

            with (
                patch("skim_core.crawlers.auth.cdp.platform.system", return_value="Darwin"),
                patch(
                    "skim_core.crawlers.auth.cdp.read_keychain_password",
                    return_value="super-secret",
                ) as read_password,
            ):
                credentials = cdp.load_login_credentials_from_keychain(
                    "threads",
                    "user@example.com",
                    db_path=db_path,
                )

            self.assertEqual(
                credentials,
                ("user@example.com", "super-secret"),
            )
            read_password.assert_called_once_with("skim.desktop.threads", "user@example.com")

    @patch("skim_core.crawlers.auth.cdp.execute_cdp_command")
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

    @patch("skim_core.crawlers.auth.cdp.execute_cdp_command")
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

    @patch("skim_core.crawlers.auth.cdp.execute_cdp_command")
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
