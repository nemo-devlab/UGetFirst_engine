import unittest
from unittest.mock import patch

import config
import db
import main
from scripts import dev_notify_test


class DevNotifyTestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.subscriber = db.Subscriber(
            id="subscriber-1",
            phone="15550001111",
            email="member@example.com",
            notify_sms=True,
            notify_email=True,
            sms_consent_at="2026-01-01T00:00:00+00:00",
            plan_tier="speed",
            plan_status="active",
            keywords=["plumber"],
        )

    def test_refuses_non_dev_environment(self) -> None:
        with patch.object(config, "ENV", "prod"):
            with self.assertRaisesRegex(SystemExit, "requires ENV=dev"):
                dev_notify_test.run_notification_test(
                    requested_channel="email"
                )

    def test_rejects_unknown_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "channel must be"):
            dev_notify_test.run_notification_test(
                requested_channel="push"
            )

    def test_uses_qa_destinations_and_dispatches_requested_channels(self) -> None:
        stats = main.DispatchStats(matches_found=1, alerts_dispatched=2)
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "QA_TEST_EMAIL", "qa@example.com"),
            patch.object(config, "QA_TEST_PHONE", "+15550002222"),
            patch.object(
                dev_notify_test,
                "_find_target",
                return_value=(
                    "https://facebook.com/groups/123",
                    "123",
                    self.subscriber,
                    {"email", "sms"},
                ),
            ),
            patch.object(dev_notify_test, "_validate_provider_config"),
            patch.object(db, "start_engine_run", return_value="run-1"),
            patch.object(db, "upsert_scraped_posts"),
            patch.object(db, "finish_engine_run"),
            patch.object(
                main,
                "dispatch_posts",
                return_value=stats,
            ) as dispatch,
        ):
            result = dev_notify_test.run_notification_test(
                requested_channel="both"
            )

        test_subscriber = dispatch.call_args.args[1]["123"][0]
        self.assertEqual(test_subscriber.email, "qa@example.com")
        self.assertEqual(test_subscriber.phone, "+15550002222")
        self.assertEqual(dispatch.call_args.kwargs["channels"], {"email", "sms"})
        self.assertEqual(result["channels"], ["email", "sms"])
        self.assertEqual(result["dispatched"], 2)
        previews = result["previews"]
        assert isinstance(previews, dict)
        self.assertIn("email", previews)
        self.assertIn("sms", previews)
        email_preview = previews["email"]
        assert isinstance(email_preview, dict)
        self.assertIn("UGetFirst", email_preview["html"])
        self.assertIn("#00C805", email_preview["html"])
        self.assertIn("plumber", email_preview["html"])
        sms_preview = previews["sms"]
        assert isinstance(sms_preview, dict)
        self.assertIn("plumber", sms_preview["body"])

    def test_email_only_clears_real_member_phone(self) -> None:
        stats = main.DispatchStats(matches_found=1, alerts_dispatched=1)
        with (
            patch.object(config, "ENV", "dev"),
            patch.object(config, "QA_TEST_EMAIL", "qa@example.com"),
            patch.object(config, "QA_TEST_PHONE", "+15550002222"),
            patch.object(
                dev_notify_test,
                "_find_target",
                return_value=(
                    "https://facebook.com/groups/123",
                    "123",
                    self.subscriber,
                    {"email", "sms"},
                ),
            ),
            patch.object(dev_notify_test, "_validate_provider_config"),
            patch.object(db, "start_engine_run", return_value="run-1"),
            patch.object(db, "upsert_scraped_posts"),
            patch.object(db, "finish_engine_run"),
            patch.object(
                main,
                "dispatch_posts",
                return_value=stats,
            ) as dispatch,
        ):
            dev_notify_test.run_notification_test(requested_channel="email")

        test_subscriber = dispatch.call_args.args[1]["123"][0]
        self.assertEqual(test_subscriber.email, "qa@example.com")
        self.assertEqual(test_subscriber.phone, "")


if __name__ == "__main__":
    unittest.main()
