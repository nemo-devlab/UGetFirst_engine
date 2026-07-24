import unittest
from unittest.mock import patch

import config
import notifier


class LiveDestinationTests(unittest.TestCase):
    def test_prod_allows_any_destination(self) -> None:
        with patch.object(config, "ENV", "prod"):
            self.assertTrue(notifier.is_live_destination("sms", "+15550001111"))
            self.assertTrue(
                notifier.is_live_destination("email", "customer@example.com")
            )

    def test_dev_allows_only_normalized_qa_phone(self) -> None:
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "QA_TEST_PHONE", "+1 (555) 000-1111"),
        ):
            self.assertTrue(notifier.is_live_destination("sms", "15550001111"))
            self.assertFalse(notifier.is_live_destination("sms", "+15550002222"))

    def test_dev_allows_only_case_insensitive_qa_email(self) -> None:
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "QA_TEST_EMAIL", "qa@example.com"),
        ):
            self.assertTrue(
                notifier.is_live_destination("email", " QA@EXAMPLE.COM ")
            )
            self.assertFalse(
                notifier.is_live_destination("email", "customer@example.com")
            )

    def test_dev_fails_closed_when_allowlist_is_empty(self) -> None:
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "QA_TEST_PHONE", ""),
            patch.object(config, "QA_TEST_EMAIL", ""),
        ):
            self.assertFalse(notifier.is_live_destination("sms", "+15550001111"))
            self.assertFalse(
                notifier.is_live_destination("email", "qa@example.com")
            )


if __name__ == "__main__":
    unittest.main()
