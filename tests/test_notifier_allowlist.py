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
            # Users store 10 digits without +1; QA may include country code.
            self.assertTrue(notifier.is_live_destination("sms", "5550001111"))
            self.assertTrue(notifier.is_live_destination("sms", "15550001111"))
            self.assertTrue(notifier.is_live_destination("sms", "+15550001111"))
            self.assertFalse(notifier.is_live_destination("sms", "+15550002222"))
            self.assertFalse(notifier.is_live_destination("sms", "5550002222"))

    def test_to_e164_adds_us_country_code_for_10_digits(self) -> None:
        self.assertEqual(notifier.to_e164("5550001111"), "+15550001111")
        self.assertEqual(notifier.to_e164("+1 (555) 000-1111"), "+15550001111")
        self.assertEqual(notifier.to_e164("15550001111"), "+15550001111")

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

    def test_dev_send_forces_simulated_for_non_qa_phone(self) -> None:
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "SMS_MODE", "twilio"),
            patch.object(config, "TWILIO_ACCOUNT_SID", "sid"),
            patch.object(config, "TWILIO_AUTH_TOKEN", "token"),
            patch.object(config, "TWILIO_FROM_NUMBER", "+15550000000"),
            patch.object(config, "QA_TEST_PHONE", "+15550001111"),
            patch.object(notifier, "_write_outbox") as write_outbox,
            patch.dict("sys.modules", {"twilio.rest": object()}),
        ):
            result = notifier.send(
                "15550009999", "plumber", "https://example.com/post"
            )
        self.assertEqual(result.channel, "simulated")
        self.assertEqual(result.status, "sent")
        write_outbox.assert_called_once()

    def test_dev_email_forces_simulated_for_non_qa_address(self) -> None:
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "RESEND_API_KEY", "re_test"),
            patch.object(config, "QA_TEST_EMAIL", "qa@example.com"),
            patch.object(notifier, "_write_email_outbox") as write_outbox,
            patch.object(notifier.urllib.request, "urlopen") as urlopen,
        ):
            result = notifier.send_email_alert(
                "customer@example.com",
                "plumber",
                "https://example.com/post",
            )
        self.assertEqual(result.channel, "simulated")
        self.assertEqual(result.status, "sent")
        write_outbox.assert_called_once()
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
